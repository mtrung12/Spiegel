"""
Graph API routes.
State is persisted server-side through the project context.
"""

import os
import re
import traceback
import threading
from contextlib import ExitStack, nullcontext
from datetime import datetime, timezone
from flask import request, jsonify

from . import graph_bp
from ..config import Config
from ..services.corpus import render_distribution
from ..services.corpus_ingest import ingest_corpus
from ..services.ontology_generator import OntologyGenerator
from ..services.graph_builder import GraphBuilderService
from ..services.text_processor import TextProcessor
from ..utils.file_parser import FileParser
from ..utils.logger import get_logger
from ..utils.locale import t, get_locale, set_locale
from ..utils.pipeline_logger import pipeline_log
from ..utils.graph_lifecycle import get_graph_readers, graph_lifecycle_lock
from ..models.task import TaskCancelled, TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus
from ..services.simulation_manager import SimulationManager
from ..services.simulation_runner import SimulationRunner, RunnerStatus
from ..services.report_agent import ReportManager, ReportStatus
from ..services.graph_memory_updater import GraphMemoryManager
from ..utils.llm_client import LLMResponseError

# Logger
logger = get_logger('spiegel.api')

# A brief shorter than this carries no usable content. Its real job is catching
# the image-only PDF that extracted to nothing, which otherwise passes silently
# and produces an ontology, an audience and a report built on an empty string.
MIN_DOCUMENT_CHARS = 200

# Chunks per ingestion call. Zep took 350 because that was its Batch API's
# per-request cap and the work happened elsewhere; here each batch is extraction
# and resolution running locally, so the number is a progress-reporting and
# cancellation granularity choice - smaller means a stop lands sooner.
GRAPH_BATCH_SIZE = 20

_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()


class GraphInUseError(RuntimeError):
    pass


def _active_graph_consumers(graph_id: str) -> list[str]:
    active = {
        f"report:{reader_id}"
        for reader_id in get_graph_readers(graph_id)
    }
    for simulation_id in GraphMemoryManager.get_simulation_ids_for_graph(graph_id):
        finalization_lock = SimulationRunner._finalization_lock(simulation_id)
        if not finalization_lock.acquire(blocking=False):
            active.add(simulation_id)
            continue
        try:
            run_state = SimulationRunner.get_run_state(simulation_id)
            if run_state and run_state.runner_status == RunnerStatus.FAILED:
                # reset/delete is the explicit recovery path for an incomplete,
                # non-replayable write. Serialize it against a retry drain.
                GraphMemoryManager.discard_inactive_updater(simulation_id)
                SimulationRunner._graph_memory_enabled.pop(simulation_id, None)
                continue
            active.add(simulation_id)
        finally:
            finalization_lock.release()
    active_runner_statuses = {
        RunnerStatus.STARTING,
        RunnerStatus.RUNNING,
        RunnerStatus.PAUSED,
        RunnerStatus.STOPPING,
    }
    for simulation in SimulationManager().list_simulations():
        if simulation.graph_id != graph_id:
            continue
        run_state = SimulationRunner.get_run_state(simulation.simulation_id)
        if run_state and run_state.runner_status in active_runner_statuses:
            active.add(simulation.simulation_id)
    return sorted(active)


def _delete_graph_if_present(graph_id: str | None) -> None:
    """Delete a referenced graph partition once no consumer still holds it."""

    if not graph_id:
        return
    # Keep the consumer check and the delete in one critical section. The
    # callers that also clear local references hold this re-entrant lock around
    # both operations.
    with graph_lifecycle_lock(graph_id):
        active_simulations = _active_graph_consumers(graph_id)
        if active_simulations:
            raise GraphInUseError(
                f"Graph {graph_id} is in use by active consumer(s): "
                f"{', '.join(active_simulations)}"
            )
        # Deleting a partition that holds nothing is a no-op, so unlike the
        # Cloud client there is no "already absent" case to swallow here.
        GraphBuilderService().delete_graph(graph_id)


def _clear_project_graph_reference(project) -> None:
    project.graph_id = None
    project.graph_build_task_id = None
    project.error = None


def _project_build_lock(project_id: str) -> threading.Lock:
    with _build_locks_guard:
        return _build_locks.setdefault(project_id, threading.Lock())


_ACTIVE_RUNNER_STATUSES = {
    RunnerStatus.STARTING,
    RunnerStatus.RUNNING,
    RunnerStatus.PAUSED,
    RunnerStatus.STOPPING,
}


def _latest_report_ids() -> dict[str, str]:
    """Map simulation_id to its newest report_id in one pass over the reports."""

    # list_reports already sorts newest first, so the first hit per simulation
    # wins. One scan for the whole page beats one scan per project.
    latest: dict[str, str] = {}
    for report in ReportManager.list_reports(limit=None):
        if report.simulation_id:
            latest.setdefault(report.simulation_id, report.report_id)
    return latest


def _project_stage(project, simulation, run_state, report_id: str | None) -> str:
    """Where the project left off, as the step the user should return to."""

    if project.status == ProjectStatus.FAILED:
        return "failed"
    if report_id:
        return "report"
    if simulation is not None:
        started = run_state is not None and (
            run_state.current_round > 0
            or run_state.runner_status != RunnerStatus.IDLE
        )
        return "run" if started or simulation.config_generated else "simulation"
    if project.status == ProjectStatus.GRAPH_COMPLETED:
        return "simulation"
    if project.ontology:
        return "graph"
    return "upload"


# Where each stage sits on the five-step stepper in the UI. Derived from the
# same _project_stage the home page uses, so "continue" and the stepper can
# never disagree about how far a project got.
_STAGE_FURTHEST_STEP = {
    "upload": 1,
    "graph": 1,
    "failed": 1,
    "simulation": 2,
    "run": 3,
    "report": 4,
}

WORKFLOW_STEP_COUNT = 5


def _furthest_step(stage: str, report_id: str | None) -> int:
    """
    The last step this project reached - where "open project" should land.

    Shared by the project list and the stepper so the button and the tabs can
    never disagree about how far a project got.
    """
    step = _STAGE_FURTHEST_STEP.get(stage, 1)

    # A finished report is the end of the workflow, so a completed project
    # opens on the last step. One still generating, or failed, has nothing for
    # step 5 to read, so it stops at the report itself.
    if report_id:
        report = ReportManager.get_report(report_id)
        if report is not None and report.status == ReportStatus.COMPLETED:
            step = WORKFLOW_STEP_COUNT
    return step


def _project_progress(project, simulations: list, report_ids: dict[str, str]) -> dict:
    """
    Which of the five workflow steps this project may navigate to.

    The stepper cannot work this out on its own: each view only holds the ids
    its own route was given, so step 1 has no idea a finished run and report
    exist and greys out every later step. Answering it server-side, from the
    stored project/simulation/report state, is what lets a returning user jump
    back to where they left off - and keeps steps that never started closed,
    since routing into one lands on a page with no id to load.
    """
    simulation = max(simulations, key=lambda s: s.created_at, default=None)
    simulation_id = simulation.simulation_id if simulation else None
    run_state = (
        SimulationRunner.get_run_state(simulation_id) if simulation_id else None
    )
    report_id = report_ids.get(simulation_id) if simulation_id else None

    stage = _project_stage(project, simulation, run_state, report_id)
    furthest_step = _furthest_step(stage, report_id)

    # The step that still has work to do - the one allowed to keep its buttons,
    # every other step being there to read. Usually the furthest one, but not
    # when a simulation is prepared and never started: that opens step 3 to
    # look at, while the launch button that would fill it still lives back on
    # step 2. Without the distinction that project locks itself out - step 2
    # read-only, step 3 with nothing to run.
    editable_step = furthest_step
    if stage == "run" and run_state is None:
        editable_step = 2
    elif stage == "report" and report_id:
        # A failed report is not progress: the button that would write another
        # one is step 3's, so that is the step that must stay live. Otherwise a
        # failed report is a dead end - nothing to read on step 4, nothing to
        # press on step 3.
        report = ReportManager.get_report(report_id)
        if report is not None and report.status == ReportStatus.FAILED:
            editable_step = 3

    # The id each step's route needs to load anything.
    route_ids = {
        1: project.project_id,
        2: simulation_id,
        3: simulation_id,
        4: report_id,
        5: report_id,
    }

    return {
        "project_id": project.project_id,
        "simulation_id": simulation_id,
        "report_id": report_id,
        "stage": stage,
        "furthest_step": furthest_step,
        "editable_step": editable_step,
        "steps": [
            {
                "step": step,
                # Reached before *and* routable. Both matter: a step the run
                # never got to has no id, and an id alone does not mean the
                # step was ever started.
                "reachable": step <= furthest_step and route_ids[step] is not None,
                "route_id": route_ids[step],
            }
            for step in range(1, WORKFLOW_STEP_COUNT + 1)
        ],
    }


def _project_overview(project, simulations: list, report_ids: dict[str, str]) -> dict:
    """Project row for the home page: metadata plus its latest run and report."""

    # Newest run wins: that is the one "continue" should resume.
    simulation = max(simulations, key=lambda s: s.created_at, default=None)
    overview = project.to_dict()

    if simulation is None:
        overview.update({
            "simulation_id": None,
            "simulation_status": None,
            "runner_status": "idle",
            "current_round": 0,
            "total_rounds": 0,
            "total_agents": None,
            "report_id": None,
            "simulation_count": 0,
        })
        overview["stage"] = _project_stage(project, None, None, None)
        overview["furthest_step"] = _furthest_step(overview["stage"], None)
        return overview

    run_state = SimulationRunner.get_run_state(simulation.simulation_id)
    report_id = report_ids.get(simulation.simulation_id)

    # Without a user-set round count, fall back to what the config recommends.
    config = SimulationManager().get_simulation_config(simulation.simulation_id) or {}
    time_config = config.get("time_config", {})
    recommended_rounds = int(
        time_config.get("total_simulation_hours", 0) * 60
        / max(time_config.get("minutes_per_round", 60), 1)
    )
    total_rounds = run_state.total_rounds if run_state and run_state.total_rounds > 0 else recommended_rounds

    # None (not 0) when agent_configs hasn't been generated yet, so the
    # frontend can tell "not decided yet" apart from "zero agents".
    total_agents = len(config["agent_configs"]) if "agent_configs" in config else None

    overview.update({
        "simulation_id": simulation.simulation_id,
        "simulation_status": simulation.status.value,
        "simulation_requirement": (
            config.get("simulation_requirement")
            or project.simulation_requirement
            or ""
        ),
        "runner_status": run_state.runner_status.value if run_state else "idle",
        "current_round": run_state.current_round if run_state else 0,
        "total_rounds": total_rounds,
        "total_agents": total_agents,
        "report_id": report_id,
        "simulation_count": len(simulations),
    })
    overview["stage"] = _project_stage(project, simulation, run_state, report_id)
    overview["furthest_step"] = _furthest_step(overview["stage"], report_id)
    return overview


def _project_has_active_build(project) -> bool:
    if project.status != ProjectStatus.GRAPH_BUILDING:
        return False
    if not project.graph_build_task_id:
        return False
    task = TaskManager().get_task(project.graph_build_task_id)
    return bool(
        task
        and task.status in {TaskStatus.PENDING, TaskStatus.PROCESSING}
    )


def allowed_file(filename: str) -> bool:
    """Check whether the file extension is allowed."""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS


# ============== Project management endpoints ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
def get_project(project_id: str):
    """
    Return the project detail.

    ``?include_text=1`` adds the extracted document text. It is opt-in because
    it is the whole brief - tens to hundreds of KB - and most callers of this
    endpoint only want the ontology and the status.
    """
    project = ProjectManager.get_project(project_id)

    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id)
        }), 404

    data = project.to_dict()
    if request.args.get('include_text') in ('1', 'true'):
        data['extracted_text'] = ProjectManager.get_extracted_text(project_id) or ''

    return jsonify({
        "success": True,
        "data": data
    })


@graph_bp.route('/project/list', methods=['GET'])
def list_projects():
    """
    List every project, enriched with its latest run and report.

    This powers the home page, so each row carries what "continue" needs:
    the newest simulation, its progress, the report it produced, and the
    stage the project stopped at (upload/graph/simulation/run/report/failed).
    """
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)

    # Group the simulations by project once, rather than per project.
    simulations_by_project: dict[str, list] = {}
    for simulation in SimulationManager().list_simulations():
        simulations_by_project.setdefault(simulation.project_id, []).append(simulation)
    report_ids = _latest_report_ids()

    return jsonify({
        "success": True,
        "data": [
            _project_overview(
                project,
                simulations_by_project.get(project.project_id, []),
                report_ids,
            )
            for project in projects
        ],
        "count": len(projects)
    })


@graph_bp.route('/project/<project_id>/progress', methods=['GET'])
def get_project_progress(project_id: str):
    """
    Which workflow steps this project can navigate to.

    Powers the stepper, which otherwise has to guess from whichever ids the
    current view happens to hold.
    """
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id),
        }), 404

    simulations = [
        simulation
        for simulation in SimulationManager().list_simulations(project_id=project_id)
    ]
    return jsonify({
        "success": True,
        "data": _project_progress(project, simulations, _latest_report_ids()),
    })


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
def delete_project(project_id: str):
    with _project_build_lock(project_id):
        return _delete_project_impl(project_id)


def _delete_project_impl(project_id: str):
    """
    Delete a project.
    """
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id)
        }), 404
    if _project_has_active_build(project):
        return jsonify({
            "success": False,
            "error": t('api.graphBuilding')
        }), 409

    # The simulations belong to the project, so they go with it. A running one
    # is not safe to delete underneath its subprocess: refuse instead.
    manager = SimulationManager()
    simulations = manager.list_simulations(project_id=project_id)
    running = [
        simulation.simulation_id
        for simulation in simulations
        if (run_state := SimulationRunner.get_run_state(simulation.simulation_id))
        and run_state.runner_status in _ACTIVE_RUNNER_STATUSES
    ]
    if running:
        return jsonify({
            "success": False,
            "error": t('api.simulationRunning', id=', '.join(running))
        }), 409

    graph_id = project.graph_id
    graph_guard = (
        graph_lifecycle_lock(graph_id) if graph_id else nullcontext()
    )
    with graph_guard:
        try:
            _delete_graph_if_present(graph_id)
        except GraphInUseError as error:
            return jsonify({"success": False, "error": str(error)}), 409
        # The local reference remains protected until it is removed, so a new
        # simulation cannot claim the just-deleted graph in between.
        success = ProjectManager.delete_project(project_id)

    if success:
        # ponytail: reports are left alone on purpose - they are the output the
        # user came for, and orphaned report rows are already tolerated.
        for simulation in simulations:
            manager.delete_simulation(simulation.simulation_id)

    if not success:
        return jsonify({
            "success": False,
            "error": t('api.projectDeleteFailed', id=project_id)
        }), 404

    return jsonify({
        "success": True,
        "message": t('api.projectDeleted', id=project_id)
    })


@graph_bp.route('/project/<project_id>/reset', methods=['POST'])
def reset_project(project_id: str):
    with _project_build_lock(project_id):
        return _reset_project_impl(project_id)


def _reset_project_impl(project_id: str):
    """
    Reset the project status, so the graph can be rebuilt.
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id)
        }), 404

    if _project_has_active_build(project):
        return jsonify({
            "success": False,
            "error": t('api.graphBuilding')
        }), 409

    graph_id = project.graph_id
    graph_guard = (
        graph_lifecycle_lock(graph_id) if graph_id else nullcontext()
    )
    with graph_guard:
        try:
            _delete_graph_if_present(graph_id)
        except GraphInUseError as error:
            return jsonify({"success": False, "error": str(error)}), 409

        # Reset back to the ontology-generated state
        if project.ontology:
            project.status = ProjectStatus.ONTOLOGY_GENERATED
        else:
            project.status = ProjectStatus.CREATED

        _clear_project_graph_reference(project)
        ProjectManager.save_project(project)
    
    return jsonify({
        "success": True,
        "message": t('api.projectReset', id=project_id),
        "data": project.to_dict()
    })


# ============== Endpoint 1: upload files and generate the ontology ==============

def _classify_ontology_error(error: Exception) -> tuple[str, int]:
    """
    Turn an ontology failure into a message safe to show and an HTTP status.

    Provider exception bodies may echo the request content back, so nothing
    derived from them reaches the caller except a status code and a scrubbed
    request id; the detail stays in the server log.
    """
    provider_status = getattr(error, "status_code", None)
    request_id = getattr(error, "request_id", None)

    if isinstance(error, LLMResponseError):
        logger.exception("LLM returned an unusable ontology response")
        return str(error), 502

    if isinstance(provider_status, int):
        public_error = f"LLM provider request failed (HTTP {provider_status})"
        if request_id:
            safe_request_id = re.sub(r"[^a-zA-Z0-9._:-]", "", str(request_id))[:128]
            if safe_request_id:
                public_error += f" (request_id: {safe_request_id})"
        logger.error(
            "Ontology provider request failed: type=%s status=%s request_id=%s",
            type(error).__name__,
            provider_status,
            request_id or "unknown",
        )
        return public_error, 502

    logger.exception("Unexpected ontology generation failure")
    return "Ontology generation failed; check the server logs", 500


def _public_ontology_error(error: Exception, project_id: str) -> str:
    """The background-task half of the above: message only, no status code."""
    public_error, response_status = _classify_ontology_error(error)
    pipeline_log.action(
        'api.graph', 'ontology_generate_failed',
        status='error',
        target=project_id,
        metrics={'http_status': response_status},
        error=f"{type(error).__name__}: {error}",
    )
    return public_error


def _start_ontology_task(
    project,
    document_texts: list,
    simulation_requirement: str,
    additional_context: str,
) -> str:
    """
    Record the project and hand the LLM call to a background thread.

    Returns the task id to poll. The caller must have saved everything the task
    does not own - files, extracted text, the requirement - because this saves
    the project once and then the task owns it.
    """
    task_manager = TaskManager()
    task_id = task_manager.create_task(
        f"generating ontology: {project.name}",
        metadata={"project_id": project.project_id},
    )
    project.ontology_task_id = task_id
    project.error = None
    ProjectManager.save_project(project)
    logger.info(
        f"created ontology task: task_id={task_id}, project_id={project.project_id}"
    )

    # Captured before the thread starts: the request context, and the locale
    # bound to it, are gone by the time the task runs.
    current_locale = get_locale()
    project_id = project.project_id

    def ontology_task():
        # A background thread starts with no request context, so the locale is
        # carried across explicitly rather than inherited.
        set_locale(current_locale)
        try:
            task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=10,
                message=t('progress.generatingOntology'),
            )
            logger.info("calling the LLM to generate the ontology definition...")
            generator = OntologyGenerator()
            ontology = generator.generate(
                document_texts=document_texts,
                simulation_requirement=simulation_requirement,
                additional_context=additional_context if additional_context else None
            )

            entity_count = len(ontology.get("entity_types", []))
            edge_count = len(ontology.get("edge_types", []))
            logger.info(
                f"ontology generated: {entity_count} entity types, "
                f"{edge_count} edge types"
            )

            # Re-read rather than closing over `project`: the request thread
            # saved it after this closure captured it.
            saved = ProjectManager.get_project(project_id)
            if saved is None:
                logger.warning(
                    "project %s vanished while its ontology was generating",
                    project_id,
                )
                task_manager.fail_task(task_id, t('api.projectNotFound', id=project_id))
                return

            saved.ontology = {
                "entity_types": ontology.get("entity_types", []),
                "edge_types": ontology.get("edge_types", [])
            }
            saved.analysis_summary = ontology.get("analysis_summary", "")
            saved.status = ProjectStatus.ONTOLOGY_GENERATED
            ProjectManager.save_project(saved)
            logger.info(f"=== ontology generated === project_id: {project_id}")

            task_manager.complete_task(task_id, {
                "project_id": project_id,
                "ontology": saved.ontology,
                "analysis_summary": saved.analysis_summary,
            })
        except Exception as task_error:
            public_error = _public_ontology_error(task_error, project_id)
            failed = ProjectManager.get_project(project_id)
            if failed is not None:
                failed.status = ProjectStatus.FAILED
                failed.error = public_error
                try:
                    ProjectManager.save_project(failed)
                except Exception:
                    logger.exception(
                        "Failed to persist ontology failure for project %s",
                        project_id,
                    )
            task_manager.fail_task(task_id, public_error)

    threading.Thread(target=ontology_task, daemon=True).start()
    return task_id


@graph_bp.route('/ontology/generate', methods=['POST'])
def generate_ontology():
    """
    Endpoint 1: upload files, then derive the ontology in the background.

    Request: multipart/form-data

    Parameters:
        files: uploaded files (PDF/MD/TXT); several are allowed
        simulation_requirement: the simulation requirement (required)
        project_name: project name (optional)
        additional_context: extra notes (optional)

    Returns as soon as the files are stored - the ontology itself is not ready
    yet, and `task_id` is what reports on it (GET /api/graph/task/<task_id>):
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "project_name": "...",
                "task_id": "task_xxxx",
                "status": "created",
                "files": [...],
                "total_text_length": 12345
            }
        }
    """
    project = None
    # The run scope only opens once the project exists, because the project id
    # is what correlates these records with the later graph build.
    log_scope = ExitStack()
    try:
        logger.info("=== generating ontology definition ===")
        
        # Read the parameters. The audience description is no longer a field of
        # its own: a campaign brief already contains it, and two sources for one
        # fact only gave them a way to disagree. It is taken from the documents
        # below instead, so the ontology and the graph read the same words.
        simulation_requirement = request.form.get('simulation_requirement', '')
        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')

        logger.debug(f"project name: {project_name}")

        # Read the uploaded files
        uploaded_files = request.files.getlist('files')
        if not uploaded_files or all(not f.filename for f in uploaded_files):
            return jsonify({
                "success": False,
                "error": t('api.requireFileUpload')
            }), 400
        
        # Create the project
        project = ProjectManager.create_project(name=project_name)
        logger.info(f"created project: {project.project_id}")
        
        # Save the files and extract their text
        document_texts = []
        all_text = ""

        for file in uploaded_files:
            if file and file.filename and allowed_file(file.filename):
                # Save the file into the project directory
                file_info = ProjectManager.save_file_to_project(
                    project.project_id,
                    file,
                    file.filename
                )
                project.files.append({
                    "filename": file_info["original_filename"],
                    "size": file_info["size"]
                })

                # Extract the text
                with pipeline_log.step(
                    'FileParser', 'extract_text',
                    target=file_info["original_filename"],
                    size_bytes=file_info["size"],
                ) as step:
                    text = FileParser.extract_text(file_info["path"])
                    text = TextProcessor.preprocess_text(text)
                    step.output_text(text)
                    step.metric(chars=len(text))
                # An empty extraction is worse than an error: the ontology, the
                # personas and the whole report would still be generated, from
                # nothing. Refuse the file instead of building on air.
                if len(text) < MIN_DOCUMENT_CHARS:
                    ProjectManager.delete_project(project.project_id)
                    return jsonify({
                        "success": False,
                        "error": t(
                            'api.documentHasNoText',
                            filename=file_info["original_filename"],
                            chars=len(text),
                        ),
                    }), 400

                document_texts.append(text)
                all_text += f"\n\n=== {file_info['original_filename']} ===\n{text}"

        if not document_texts:
            ProjectManager.delete_project(project.project_id)
            return jsonify({
                "success": False,
                "error": t('api.noDocProcessed')
            }), 400
        
        # Persist the extracted text
        project.total_text_length = len(all_text)
        ProjectManager.save_extracted_text(project.project_id, all_text)
        logger.info(f"text extraction complete, {len(all_text)} characters")

        # Everything downstream - the ontology prompt, the population hints, the
        # config generator, the report agent - reads this as "the brief". With
        # no field of its own it is the documents, which is what a caller who
        # pasted their brief into the old box was providing anyway.
        if not simulation_requirement:
            simulation_requirement = all_text.strip()
        project.simulation_requirement = simulation_requirement

        # Everything above is fast and its failures are the caller's to fix, so
        # it stays in the request. The LLM call is neither: it runs for minutes,
        # and while it did this response held the only copy of the new project
        # id. A reload in that window stranded the client on /process/new with
        # its in-memory file list gone and nothing to recover from - the upload
        # was simply lost. The id and a task to poll now come back immediately.
        task_id = _start_ontology_task(
            project, document_texts, simulation_requirement, additional_context
        )

        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "project_name": project.name,
                "task_id": task_id,
                "status": project.status.value,
                "files": project.files,
                "total_text_length": project.total_text_length
            }
        })

    except Exception as error:
        public_error, response_status = _classify_ontology_error(error)

        pipeline_log.action(
            'api.graph', 'ontology_generate_failed',
            status='error',
            target=project.project_id if project is not None else None,
            metrics={'http_status': response_status},
            error=f"{type(error).__name__}: {error}",
        )

        response_data = None
        if project is not None:
            project.status = ProjectStatus.FAILED
            project.error = public_error
            try:
                ProjectManager.save_project(project)
            except Exception:
                logger.exception(
                    "Failed to persist ontology failure for project %s",
                    project.project_id,
                )
            response_data = {"project_id": project.project_id}

        payload = {
            "success": False,
            "error": public_error,
        }
        if response_data is not None:
            payload["data"] = response_data
        return jsonify(payload), response_status

    finally:
        log_scope.close()


@graph_bp.route('/ontology/retry', methods=['POST'])
def retry_ontology():
    """
    Re-run ontology generation for a project whose first attempt did not land.

    The task manager keeps tasks in memory, so a backend restart mid-generation
    leaves a project stuck at `created` pointing at a task id that no longer
    resolves - a state the client can only poll forever. The uploaded documents
    and the extracted text are on disk, so nothing needs re-uploading to try
    again; this is also the recovery path for an ontology that simply failed.

    Request (JSON): {"project_id": "proj_xxxx"}
    Returns: {"success": true, "data": {"project_id": ..., "task_id": ...}}
    """
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id')
    if not project_id:
        return jsonify({"success": False, "error": t('api.requireProjectId')}), 400

    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id)
        }), 404

    # Only the states this can actually repair. A project that already has an
    # ontology moves on through /build; regenerating it here would orphan a
    # graph that was built from the old one.
    if project.status not in (ProjectStatus.CREATED, ProjectStatus.FAILED):
        return jsonify({
            "success": False,
            "error": t('api.ontologyRetryNotApplicable', status=project.status.value)
        }), 409

    # An attempt that is genuinely still running must not be duplicated.
    if project.ontology_task_id:
        existing = TaskManager().get_task(project.ontology_task_id)
        if existing is not None and existing.status not in TERMINAL_STATUSES:
            return jsonify({
                "success": True,
                "data": {
                    "project_id": project_id,
                    "task_id": project.ontology_task_id,
                    "reused": True,
                }
            })

    extracted_text = ProjectManager.get_extracted_text(project_id)
    if not extracted_text:
        return jsonify({"success": False, "error": t('api.textNotFound')}), 400

    project.status = ProjectStatus.CREATED
    task_id = _start_ontology_task(
        project,
        [extracted_text],
        project.simulation_requirement or extracted_text.strip(),
        '',
    )

    return jsonify({
        "success": True,
        "data": {"project_id": project_id, "task_id": task_id}
    })


# ============== Endpoint 2: build the graph ==============

@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """Serialize build claims for the same project within this process."""

    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id")
    if not project_id:
        return _build_graph_impl()
    with _project_build_lock(project_id):
        return _build_graph_impl()


def _build_graph_impl():
    """
    Endpoint 2: build the graph for a project_id.
    
    Request (JSON):
        {
            "project_id": "proj_xxxx",  // required, returned by endpoint 1
            "graph_name": "graph name",  // optional
            "chunk_size": 500,          // optional, default 500
            "chunk_overlap": 50         // optional, default 50
        }
        
    Returns:
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "task_id": "task_xxxx",
                "message": "graph build task started"
            }
        }
    """
    try:
        logger.info("=== building graph ===")
        
        # Validate the configuration
        errors = []
        if not Config.NEO4J_PASSWORD:
            errors.append(t('api.graphStoreNotConfigured'))
        if errors:
            logger.error(f"configuration errors: {errors}")
            return jsonify({
                "success": False,
                "error": t('api.configError', details="; ".join(errors))
            }), 500
        
        # Parse the request
        data = request.get_json() or {}
        project_id = data.get('project_id')
        logger.debug(f"request parameters: project_id={project_id}")
        
        if not project_id:
            return jsonify({
                "success": False,
                "error": t('api.requireProjectId')
            }), 400
        
        # Load the project
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=project_id)
            }), 404

        # Check the project status
        force = data.get('force', False)  # Force a rebuild
        if not isinstance(force, bool):
            return jsonify({
                "success": False,
                "error": "force must be a JSON boolean"
            }), 400
        
        if project.status == ProjectStatus.CREATED:
            return jsonify({
                "success": False,
                "error": t('api.ontologyNotGenerated')
            }), 400
        
        if project.status == ProjectStatus.GRAPH_BUILDING:
            if _project_has_active_build(project):
                return jsonify({
                    "success": True,
                    "data": {
                        "project_id": project_id,
                        "task_id": project.graph_build_task_id,
                        "graph_id": project.graph_id,
                        "reused": True,
                        "message": t('api.graphBuilding')
                    }
                })

            # GRAPH_BUILDING with no live task means the process died mid-build.
            # Ingestion runs in that process, so nothing survived it to resume -
            # the partial graph is dropped and the build starts over. Under Zep
            # this was recoverable, because their queue kept working without us.
            project.status = ProjectStatus.FAILED
            project.error = (
                "The graph build did not finish and cannot be resumed; "
                "rebuild the graph"
            )
            ProjectManager.save_project(project)
            if not force:
                return jsonify({
                    "success": False,
                    "error": project.error,
                    "task_id": project.graph_build_task_id,
                    "recoverable": True,
                }), 409

        if project.status == ProjectStatus.GRAPH_COMPLETED and not force:
            return jsonify({
                "success": True,
                "data": {
                    "project_id": project_id,
                    "task_id": project.graph_build_task_id,
                    "graph_id": project.graph_id,
                    "reused": True,
                    "message": t('progress.graphBuildComplete')
                }
            })
        
        # Read the settings
        graph_name = data.get('graph_name', project.name or 'Spiegel Graph')
        chunk_size = data.get('chunk_size', project.chunk_size or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = data.get('chunk_overlap', project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP)
        if not isinstance(chunk_size, int) or chunk_size <= 0:
            return jsonify({"success": False, "error": "chunk_size must be a positive integer"}), 400
        if (
            not isinstance(chunk_overlap, int)
            or chunk_overlap < 0
            or chunk_overlap >= chunk_size
        ):
            return jsonify({
                "success": False,
                "error": "chunk_overlap must satisfy 0 <= chunk_overlap < chunk_size"
            }), 400
        
        # Update the project settings
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap
        
        # Load the extracted text
        text = ProjectManager.get_extracted_text(project_id)
        if not text:
            return jsonify({
                "success": False,
                "error": t('api.textNotFound')
            }), 400

        # Projects created since the audience field was removed carry the
        # documents themselves as the requirement, so this is a no-op for them.
        # It still matters for older projects, where someone typed an audience
        # description the uploaded deck never contained: that text reached the
        # ontology prompt but never the graph, so a segment the deck named once
        # in a layout table had nothing to reinforce it and was not extracted.
        # Both sides are normalised before the comparison - otherwise the
        # requirement, which is now a preprocessed copy of the documents, would
        # fail a raw substring test and prepend the whole brief twice.
        requirement = TextProcessor.preprocess_text(
            project.simulation_requirement or ""
        )
        if requirement and requirement not in TextProcessor.preprocess_text(text):
            text = f"=== Campaign brief and target audience ===\n{requirement}\n\n{text}"
        
        # Load the ontology
        ontology = project.ontology
        if not ontology:
            return jsonify({
                "success": False,
                "error": t('api.ontologyNotFound')
            }), 400

        # Only mutate Cloud state after the complete rebuild request validates.
        if project.status == ProjectStatus.FAILED or (
            force and project.status == ProjectStatus.GRAPH_COMPLETED
        ):
            graph_id_to_delete = project.graph_id
            graph_guard = (
                graph_lifecycle_lock(graph_id_to_delete)
                if graph_id_to_delete
                else nullcontext()
            )
            with graph_guard:
                _delete_graph_if_present(graph_id_to_delete)
                project.status = ProjectStatus.ONTOLOGY_GENERATED
                _clear_project_graph_reference(project)
                ProjectManager.save_project(project)
        
        # Create the background task
        task_manager = TaskManager()
        task_id = task_manager.create_task(f"building graph: {graph_name}")
        logger.info(f"created graph build task: task_id={task_id}, project_id={project_id}")
        
        # Update the project status
        project.status = ProjectStatus.GRAPH_BUILDING
        project.graph_build_task_id = task_id
        ProjectManager.save_project(project)
        
        # Capture locale before spawning background thread
        current_locale = get_locale()

        # Start the background task
        def build_task():
            # A background thread starts with an empty logging context, so the
            # run scope is opened here rather than inherited from the request.
            set_locale(current_locale)
            build_logger = get_logger('spiegel.build')

            def check_cancelled():
                """Stop here if the user pressed stop. Safe between stages only."""
                task_manager.raise_if_cancelled(task_id)

            def set_corpus_state(state, **fields):
                """Publish the crawler's coarse state for the build card."""
                task_manager.update_task(
                    task_id,
                    progress_detail={'corpus': {'state': state, **fields}},
                )

            try:
                build_logger.info(f"[{task_id}] building graph...")
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    message=t('progress.initGraphService')
                )
                check_cancelled()
                
                # Create the graph build service
                builder = GraphBuilderService()
                
                # Harvest public discussion about the category and code it into
                # a theme distribution. Best-effort: a build that cannot reach
                # the sources still proceeds on the brief alone.
                build_text = text

                def corpus_progress(message, state=None, **fields):
                    task_manager.update_task(task_id, message=message, progress=3)
                    if state:
                        set_corpus_state(state, **fields)

                set_corpus_state('starting')
                with pipeline_log.step(
                    'CorpusIngest', 'ingest_corpus', target=project_id,
                ) as step:
                    corpus = ingest_corpus(
                        text,
                        progress=corpus_progress,
                        should_cancel=lambda: task_manager.is_cancelled(task_id),
                    )
                    step.metric(**{
                        k: v for k, v in corpus['summary'].items()
                        if isinstance(v, (int, float, str))
                    })

                project.corpus_distribution = corpus['distribution']
                project.corpus_summary = corpus['summary']
                ProjectManager.save_project(project)

                if corpus['episode_text']:
                    # Appended as text so the harvested priors travel through
                    # the same chunk-and-episode path as the brief.
                    build_text = f"{text}\n\n{corpus['episode_text']}"
                    set_corpus_state(
                        'done',
                        themes=corpus['summary'].get('themes', 0),
                        coded=corpus['summary'].get('coded', 0),
                    )
                else:
                    set_corpus_state(
                        'skipped',
                        reason=corpus['summary'].get('skipped', 'no_results'),
                    )

                check_cancelled()

                # Chunk the text
                task_manager.update_task(
                    task_id,
                    message=t('progress.textChunking'),
                    progress=5
                )
                with pipeline_log.step(
                    'TextProcessor', 'split_text',
                    chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                ) as step:
                    step.input_text(build_text)
                    chunks = TextProcessor.split_text(
                        build_text,
                        chunk_size=chunk_size,
                        overlap=chunk_overlap
                    )
                    builder.validate_batch_chunks(chunks, batch_size=GRAPH_BATCH_SIZE)
                    total_chunks = len(chunks)
                    step.metric(chunks=total_chunks, text_chars=len(build_text))
                    step.output(first_chunk_preview=chunks[0][:400] if chunks else '')

                # Last point before anything is written to the graph store, so a
                # stop here leaves nothing to clean up.
                check_cancelled()

                # Claim the graph partition
                task_manager.update_task(
                    task_id,
                    message=t('progress.creatingGraph'),
                    progress=10
                )

                def remember_graph(graph_id):
                    project.graph_id = graph_id
                    ProjectManager.save_project(project)

                with pipeline_log.step(
                    'GraphBuilderService', 'create_graph', target=graph_name,
                ) as step:
                    graph_id = builder.create_graph(
                        name=graph_name,
                        graph_id_callback=remember_graph,
                    )
                    step.target(graph_id).metric(graph_id=graph_id)

                # Ingest the chunks. Extraction runs here rather than on a
                # remote queue, so this single phase covers what used to be an
                # upload followed by a wait: 10% - 90%.
                def add_progress_callback(msg, progress_ratio):
                    progress = 10 + int(progress_ratio * 80)
                    task_manager.update_task(
                        task_id,
                        message=msg,
                        progress=progress
                    )
                    # The longest phase of the build, and this runs once per
                    # batch, so a stop lands between batches.
                    check_cancelled()

                task_manager.update_task(
                    task_id,
                    message=t('progress.addingChunks', count=total_chunks),
                    progress=10
                )

                with pipeline_log.step(
                    'GraphBuilderService', 'add_text_batches', target=graph_id,
                    batch_size=GRAPH_BATCH_SIZE, chunks=total_chunks,
                ) as step:
                    step.input(ontology=ontology)
                    step.metric(
                        entity_types=len(ontology.get('entity_types') or []),
                        edge_types=len(ontology.get('edge_types') or []),
                    )
                    ingested = builder.add_text_batches(
                        graph_id,
                        chunks,
                        ontology=ontology,
                        batch_size=GRAPH_BATCH_SIZE,
                        progress_callback=add_progress_callback,
                    )
                    step.metric(items=ingested)

                # Fetch the graph data
                task_manager.update_task(
                    task_id,
                    message=t('progress.fetchingGraphData'),
                    progress=95
                )
                graph_data = builder.get_graph_data(graph_id)
                
                node_count = graph_data.get("node_count", 0)
                edge_count = graph_data.get("edge_count", 0)
                build_logger.info(f"[{task_id}] graph build complete: graph_id={graph_id}, nodes={node_count}, edges={edge_count}")

                # Publish local project/task terminal state under the same
                # lifecycle lock used by reset/delete/build claims. This
                # prevents a deletion from interleaving between the two saves.
                with _project_build_lock(project_id):
                    project.status = ProjectStatus.GRAPH_COMPLETED
                    project.error = None
                    # Everything in the graph after this instant came from a
                    # simulation writing its agents' activity back, not from
                    # the documents. The audience must be read from the
                    # documents only - see ZepEntityReader.
                    project.graph_built_at = datetime.now(timezone.utc).isoformat()
                    ProjectManager.save_project(project)
                    task_manager.update_task(
                        task_id,
                        status=TaskStatus.COMPLETED,
                        message=t('progress.graphBuildComplete'),
                        progress=100,
                        result={
                            "project_id": project_id,
                            "graph_id": graph_id,
                            "node_count": node_count,
                            "edge_count": edge_count,
                            "chunk_count": total_chunks,
                        }
                    )
                
            except TaskCancelled:
                # The user stopped the build. Whatever was already created in
                # the graph store is left in place and the project is marked FAILED,
                # which is the state reset and force-rebuild already recover
                # from - a cancelled build needs no recovery path of its own.
                build_logger.info(f"[{task_id}] graph build stopped by the user")

                with _project_build_lock(project_id):
                    project.status = ProjectStatus.FAILED
                    project.error = t('api.buildCancelled')
                    ProjectManager.save_project(project)

                    task_manager.cancel_task(task_id, t('api.buildCancelled'))

            except Exception as e:
                # Mark the project as failed
                build_logger.error(f"[{task_id}] graph build failed: {str(e)}")
                build_logger.debug(traceback.format_exc())
                
                with _project_build_lock(project_id):
                    project.status = ProjectStatus.FAILED
                    project.error = str(e)
                    ProjectManager.save_project(project)

                    task_manager.update_task(
                        task_id,
                        status=TaskStatus.FAILED,
                        message=t('progress.buildFailed', error=str(e)),
                        error=traceback.format_exc()
                    )
        
        # Start the background thread
        thread = threading.Thread(target=build_task, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                # No "resumed" flag: ingestion runs in this process, so a build
                # that was interrupted has nothing left running to rejoin. The
                # only outcomes are a fresh build or a rebuild.
                "message": t('api.graphBuildStarted', taskId=task_id)
            }
        })
        
    except GraphInUseError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception as e:
        logger.exception("request failed")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


# ============== Task query endpoints ==============

@graph_bp.route('/task/<task_id>', methods=['GET'])
def get_task(task_id: str):
    """
    Query the task status.
    """
    task = TaskManager().get_task(task_id)
    
    if not task:
        return jsonify({
            "success": False,
            "error": t('api.taskNotFound', id=task_id)
        }), 404
    
    return jsonify({
        "success": True,
        "data": task.to_dict()
    })


@graph_bp.route('/task/<task_id>/cancel', methods=['POST'])
def cancel_task(task_id: str):
    """
    Stop a running task.

    Cancellation is cooperative, so this only raises the flag - the worker
    unwinds at its next checkpoint. The response says the stop was requested,
    not that the pipeline has already come to rest; the caller keeps polling
    the task until it reports a terminal status.
    """
    task_manager = TaskManager()
    task = task_manager.get_task(task_id)

    if not task:
        return jsonify({
            "success": False,
            "error": t('api.taskNotFound', id=task_id)
        }), 404

    if not task_manager.request_cancel(task_id):
        return jsonify({
            "success": False,
            "error": t('api.taskAlreadyFinished')
        }), 409

    logger.info(f"cancellation requested for task {task_id}")
    return jsonify({
        "success": True,
        "data": {
            "task_id": task_id,
            "message": t('api.taskCancelRequested')
        }
    })


@graph_bp.route('/tasks', methods=['GET'])
def list_tasks():
    """
    List every task.
    """
    tasks = TaskManager().list_tasks()
    
    return jsonify({
        "success": True,
        "data": tasks,
        "count": len(tasks)
    })


# ============== Graph data endpoints ==============

@graph_bp.route('/data/<graph_id>', methods=['GET'])
def get_graph_data(graph_id: str):
    """
    Return the graph data: nodes and edges.
    """
    try:
        if not Config.NEO4J_PASSWORD:
            return jsonify({
                "success": False,
                "error": t('api.graphStoreNotConfigured')
            }), 500

        builder = GraphBuilderService()
        graph_data = builder.get_graph_data(graph_id)
        
        return jsonify({
            "success": True,
            "data": graph_data
        })
        
    except Exception as e:
        logger.exception("request failed")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
def delete_graph(graph_id: str):
    """
    Delete the graph.
    """
    try:
        if not Config.NEO4J_PASSWORD:
            return jsonify({
                "success": False,
                "error": t('api.graphStoreNotConfigured')
            }), 500

        projects = ProjectManager.find_projects_by_graph_id(graph_id)
        if not projects:
            return jsonify({
                "success": False,
                "error": "No local project references this graph"
            }), 404
        project_ids = sorted({project.project_id for project in projects})
        with ExitStack() as stack:
            for project_id in project_ids:
                stack.enter_context(_project_build_lock(project_id))
            stack.enter_context(graph_lifecycle_lock(graph_id))

            # Re-read under all owning project locks so a concurrent build
            # claim cannot appear between validation and Cloud deletion.
            projects = ProjectManager.find_projects_by_graph_id(graph_id)
            if any(_project_has_active_build(project) for project in projects):
                return jsonify({
                    "success": False,
                    "error": t('api.graphBuilding')
                }), 409

            _delete_graph_if_present(graph_id)

            for project in projects:
                _clear_project_graph_reference(project)
                project.status = (
                    ProjectStatus.ONTOLOGY_GENERATED
                    if project.ontology
                    else ProjectStatus.CREATED
                )
                ProjectManager.save_project(project)
        
        return jsonify({
            "success": True,
            "message": t('api.graphDeleted', id=graph_id)
        })
        
    except GraphInUseError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception as e:
        logger.exception("request failed")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500
