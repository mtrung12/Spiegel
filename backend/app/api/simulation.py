"""
Simulation API routes.
Step 2: read and filter Zep entities, then prepare and run the OASIS
simulation end to end.
"""

import os
from contextlib import nullcontext
from flask import request, jsonify, send_file

from . import simulation_bp
from ..config import Config
from ..services.agent_population import MAX_AGENTS, planned_population_size
from ..services.zep_entity_reader import ZepEntityReader
from ..services.oasis_profile_generator import OasisProfileGenerator
from ..services.simulation_manager import SimulationManager, SimulationStatus
from ..services.simulation_runner import (
    SimulationRunner,
    RunnerStatus,
    SimulationStopPending,
)
from ..services.zep_graph_memory_updater import ZepGraphMemoryManager
from ..utils.logger import get_logger
from ..utils.locale import t, get_locale, set_locale
from ..utils.zep_lifecycle import get_graph_readers, graph_lifecycle_lock
from ..models.project import ProjectManager

logger = get_logger('spiegel.api.simulation')

# Sort keys the feed board may request, mapped to the SQL expression each one
# resolves to. Whitelisted because the value is interpolated into the ORDER BY
# clause, where a bind parameter cannot be used.
POST_SORT_COLUMNS = {
    'created_at': 'p.created_at',
    'num_likes': 'p.num_likes',
    'num_dislikes': 'p.num_dislikes',
    'num_shares': 'p.num_shares',
    'num_comments': 'num_comments',
    'post_id': 'p.post_id',
}


def _get_default_platform(simulation_id: str) -> str:
    """
    Return the default platform for a simulation.

    Reads enable_twitter / enable_reddit off the SimulationState and returns
    the platform the simulation actually uses, rather than hard-coding 'reddit'.

    Args:
        simulation_id: Simulation ID

    Returns:
        'twitter' or 'reddit'
    """
    try:
        manager = SimulationManager()
        state = manager._load_simulation_state(simulation_id)
        if state:
            return state.get_default_platform()
    except Exception:
        pass
    return "reddit"


# Prefix prepended to interview prompts
# It stops the agent reaching for a tool and makes it reply in plain text
INTERVIEW_PROMPT_PREFIX = "Draw on your persona and everything you remember doing, call no tools, and answer me directly in plain text: "


def optimize_interview_prompt(prompt: str) -> str:
    """
    Prepend the prefix to an interview prompt so the agent does not call tools.
    
    Args:
        prompt: The original question
        
    Returns:
        The prefixed question
    """
    if not prompt:
        return prompt
    # Do not add the prefix twice
    if prompt.startswith(INTERVIEW_PROMPT_PREFIX):
        return prompt
    return f"{INTERVIEW_PROMPT_PREFIX}{prompt}"


# ============== Entity read endpoints ==============

@simulation_bp.route('/entities/<graph_id>', methods=['GET'])
def get_graph_entities(graph_id: str):
    """
    Return every entity in the graph, already filtered.
    
    Only nodes matching a predefined entity type are returned - that is,
    nodes whose labels are more than just Entity.
    
    Query parameters:
        entity_types: comma-separated entity types (optional, narrows the filter)
        enrich: also fetch the related edges (default true)
    """
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": t('api.zepApiKeyMissing')
            }), 500
        
        entity_types_str = request.args.get('entity_types', '')
        entity_types = [t.strip() for t in entity_types_str.split(',') if t.strip()] if entity_types_str else None
        enrich = request.args.get('enrich', 'true').lower() == 'true'
        
        logger.info(f"fetching graph entities: graph_id={graph_id}, entity_types={entity_types}, enrich={enrich}")
        
        reader = ZepEntityReader()
        result = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=enrich
        )
        
        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch graph entities: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/entities/<graph_id>/<entity_uuid>', methods=['GET'])
def get_entity_detail(graph_id: str, entity_uuid: str):
    """Return the detail for one entity."""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": t('api.zepApiKeyMissing')
            }), 500
        
        reader = ZepEntityReader()
        entity = reader.get_entity_with_context(graph_id, entity_uuid)
        
        if not entity:
            return jsonify({
                "success": False,
                "error": t('api.entityNotFound', id=entity_uuid)
            }), 404
        
        return jsonify({
            "success": True,
            "data": entity.to_dict()
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch entity detail: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/entities/<graph_id>/by-type/<entity_type>', methods=['GET'])
def get_entities_by_type(graph_id: str, entity_type: str):
    """Return every entity of a given type."""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": t('api.zepApiKeyMissing')
            }), 500
        
        enrich = request.args.get('enrich', 'true').lower() == 'true'
        
        reader = ZepEntityReader()
        entities = reader.get_entities_by_type(
            graph_id=graph_id,
            entity_type=entity_type,
            enrich_with_edges=enrich
        )
        
        return jsonify({
            "success": True,
            "data": {
                "entity_type": entity_type,
                "count": len(entities),
                "entities": [e.to_dict() for e in entities]
            }
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch entity: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


# ============== Simulation management endpoints ==============

@simulation_bp.route('/create', methods=['POST'])
def create_simulation():
    """
    Create a new simulation.
    
    Note: max_rounds and friends are generated by the LLM; they do not have
    to be set by hand.
    
    Request (JSON):
        {
            "project_id": "proj_xxxx",      // required
            "graph_id": "spiegel_xxxx",    // optional; taken from the project when omitted
            "enable_twitter": true,          // optional, default true
            "enable_reddit": true            // optional, default true
        }
    
    Returns:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "project_id": "proj_xxxx",
                "graph_id": "spiegel_xxxx",
                "status": "created",
                "enable_twitter": true,
                "enable_reddit": true,
                "created_at": "2025-12-01T10:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        project_id = data.get('project_id')
        if not project_id:
            return jsonify({
                "success": False,
                "error": t('api.requireProjectId')
            }), 400
        
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=project_id)
            }), 404
        
        graph_id = data.get('graph_id') or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": t('api.graphNotBuilt')
            }), 400
        
        manager = SimulationManager()
        state = manager.create_simulation(
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=data.get('enable_twitter', True),
            enable_reddit=data.get('enable_reddit', True),
        )
        
        return jsonify({
            "success": True,
            "data": state.to_dict()
        })
        
    except Exception as e:
        logger.exception(f"failed to create simulation: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


def _check_simulation_prepared(simulation_id: str) -> tuple:
    """
    Check whether the simulation has already been prepared.
    
    Conditions checked:
    1. state.json exists and its status is "ready"
    2. The required files exist: reddit_profiles.json, twitter_profiles.csv,
       simulation_config.json
    
    Note: the runner scripts (run_*.py) stay in backend/scripts/ and are no
    longer copied into the simulation directory.
    
    Args:
        simulation_id: Simulation ID
        
    Returns:
        (is_prepared: bool, info: dict)
    """
    import os
    from ..config import Config
    
    simulation_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
    
    # Does the directory exist?
    if not os.path.exists(simulation_dir):
        return False, {"reason": "simulation directory does not exist"}
    
    # Required files (not the scripts, which live in backend/scripts/)
    required_files = [
        "state.json",
        "simulation_config.json",
        "reddit_profiles.json",
        "twitter_profiles.csv"
    ]
    
    # Do the files exist?
    existing_files = []
    missing_files = []
    for f in required_files:
        file_path = os.path.join(simulation_dir, f)
        if os.path.exists(file_path):
            existing_files.append(f)
        else:
            missing_files.append(f)
    
    if missing_files:
        return False, {
            "reason": "required files are missing",
            "missing_files": missing_files,
            "existing_files": existing_files
        }
    
    # Check the status in state.json
    state_file = os.path.join(simulation_dir, "state.json")
    try:
        import json
        with open(state_file, 'r', encoding='utf-8') as f:
            state_data = json.load(f)
        
        status = state_data.get("status", "")
        config_generated = state_data.get("config_generated", False)
        
        # Verbose logging
        logger.debug(f"checking simulation readiness: {simulation_id}, status={status}, config_generated={config_generated}")
        
        # config_generated=True plus the files present means preparation is done.
        # All of these statuses imply preparation finished:
        # - ready: prepared and runnable
        # - preparing: finished if config_generated=True
        # - running: already running, so preparation is long done
        # - completed: run finished, so preparation is long done
        # - stopped: stopped, so preparation is long done
        # - failed: the run failed, but preparation had finished
        prepared_statuses = ["ready", "preparing", "running", "completed", "stopped", "failed"]
        if status in prepared_statuses and config_generated:
            # Gather the file statistics
            profiles_file = os.path.join(simulation_dir, "reddit_profiles.json")
            config_file = os.path.join(simulation_dir, "simulation_config.json")
            
            profiles_count = 0
            if os.path.exists(profiles_file):
                with open(profiles_file, 'r', encoding='utf-8') as f:
                    profiles_data = json.load(f)
                    profiles_count = len(profiles_data) if isinstance(profiles_data, list) else 0
            
            # Status says preparing but the files are complete: promote to ready
            if status == "preparing":
                try:
                    state_data["status"] = "ready"
                    from datetime import datetime
                    state_data["updated_at"] = datetime.now().isoformat()
                    with open(state_file, 'w', encoding='utf-8') as f:
                        json.dump(state_data, f, ensure_ascii=False, indent=2)
                    logger.info(f"auto-updating simulation status: {simulation_id} preparing -> ready")
                    status = "ready"
                except Exception as e:
                    logger.warning(f"automatic status update failed: {e}")
            
            logger.info(f"simulation {simulation_id} is prepared (status={status}, config_generated={config_generated})")
            return True, {
                "status": status,
                "entities_count": state_data.get("entities_count", 0),
                "profiles_count": profiles_count,
                "entity_types": state_data.get("entity_types", []),
                "config_generated": config_generated,
                "created_at": state_data.get("created_at"),
                "updated_at": state_data.get("updated_at"),
                "existing_files": existing_files
            }
        else:
            logger.warning(f"simulation {simulation_id} is not prepared (status={status}, config_generated={config_generated})")
            return False, {
                "reason": f"status is not in the prepared set, or config_generated is false: status={status}, config_generated={config_generated}",
                "status": status,
                "config_generated": config_generated
            }
            
    except Exception as e:
        return False, {"reason": f"failed to read the state file: {str(e)}"}


@simulation_bp.route('/prepare', methods=['POST'])
def prepare_simulation():
    """
    Prepare the simulation environment as a background task, with the LLM
    generating every parameter.
    
    This is slow, so the endpoint returns a task_id immediately.
    Poll GET /api/simulation/prepare/status for progress.
    
    Behaviour:
    - Detects work that has already been prepared and does not redo it
    - Returns the existing result when preparation is already complete
    - Supports a forced regeneration (force_regenerate=true)
    
    Steps:
    1. Check for preparation that has already finished
    2. Read and filter the entities from the Zep graph
    3. Generate an OASIS agent profile per entity, with retries
    4. Generate the simulation config with the LLM, with retries
    5. Write the config file and the preset scripts
    
    Request (JSON):
        {
            "simulation_id": "sim_xxxx",                   // required, the simulation ID
            "entity_types": ["Student", "PublicFigure"],  // optional, restrict the entity types
            "use_llm_for_profiles": true,                 // optional, build personas with the LLM
            "parallel_profile_count": 5,                  // optional, personas generated in parallel, default 5
            "max_agents": 500,                            // optional, cast size, capped at MAX_AGENTS.
                                                          // Fewer entities are cloned up to it,
                                                          // more are truncated to it
            "force_regenerate": false                     // optional, force a regeneration, default false
        }
    
    Returns:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",           // present for a newly started task
                "status": "preparing|ready",
                "message": "preparation started | already prepared",
                "already_prepared": true|false    // whether preparation had already finished
            }
        }
    """
    import threading
    import os
    from ..models.task import TaskManager, TaskStatus
    from ..config import Config
    
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        # Is a forced regeneration requested?
        force_regenerate = data.get('force_regenerate', False)
        logger.info(f"handling /prepare: simulation_id={simulation_id}, force_regenerate={force_regenerate}")
        
        # Already prepared? If so, do not redo the work
        if not force_regenerate:
            logger.debug(f"checking whether simulation {simulation_id} is prepared...")
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            logger.debug(f"check result: is_prepared={is_prepared}, prepare_info={prepare_info}")
            if is_prepared:
                logger.info(f"simulation {simulation_id} is already prepared; skipping regeneration")
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "message": t('api.alreadyPrepared'),
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
            else:
                logger.info(f"simulation {simulation_id} is not prepared; starting the preparation task")
        
        # Pull what we need off the project
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=state.project_id)
            }), 404
        
        # Read the document text
        document_text = ProjectManager.get_extracted_text(state.project_id) or ""

        # The brief. No longer a field of its own, so it falls back to the
        # documents - which is what a project created since the field was
        # removed already stores. Only a project with neither is unusable.
        simulation_requirement = project.simulation_requirement or document_text
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": t('api.projectMissingRequirement')
            }), 400
        
        entity_types_list = data.get('entity_types')
        use_llm_for_profiles = data.get('use_llm_for_profiles', True)
        parallel_profile_count = data.get('parallel_profile_count', 5)

        # Cast size. Fewer entities than this are cloned up to it, more are
        # truncated to it. Hard-capped at MAX_AGENTS whatever the caller asks.
        try:
            max_agents = int(data.get('max_agents') or MAX_AGENTS)
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": t('api.maxAgentsInvalid')
            }), 400
        if max_agents <= 0:
            return jsonify({
                "success": False,
                "error": t('api.maxAgentsPositive')
            }), 400
        max_agents = min(max_agents, MAX_AGENTS)
        
        # ===== Count the entities synchronously, before the background task starts =====
        # so the frontend knows the expected agent total as soon as prepare returns
        try:
            logger.info(f"fetching entity count synchronously: graph_id={state.graph_id}")
            reader = ZepEntityReader()
            # Fast read: only the count is needed, so skip the edges
            filtered_preview = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=entity_types_list,
                enrich_with_edges=False  # Skipping edges keeps this fast
            )
            # Store the count on the state so the frontend can read it right away
            state.entities_count = filtered_preview.filtered_count
            state.entity_types = list(filtered_preview.entity_types)
            # Same classification the real run uses, minus the hints LLM call:
            # without it every campaign-specific type falls back to
            # "specific_company" (never cloned), so the preview under-reports
            # the cast badly.
            ontology_kinds = {
                entity["name"]: entity["kind"]
                for entity in ((project.ontology or {}).get("entity_types") or [])
                if isinstance(entity, dict) and entity.get("kind") and entity.get("name")
            }
            expected_agent_count = planned_population_size(
                filtered_preview.entities, max_agents, kinds=ontology_kinds
            )
            logger.info(
                f"expected entity count: {filtered_preview.filtered_count} "
                f"-> {expected_agent_count} agents, types: {filtered_preview.entity_types}"
            )
        except Exception as e:
            logger.warning(f"synchronous entity count failed (the background task will retry): {e}")
            # A failure here is harmless; the background task will fetch it again
            expected_agent_count = None
        
        # Create the background task
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="simulation_prepare",
            metadata={
                "simulation_id": simulation_id,
                "project_id": state.project_id
            }
        )
        
        # Update the simulation state, including the entity count we just read
        state.status = SimulationStatus.PREPARING
        manager._save_simulation_state(state)
        
        # Capture locale before spawning background thread
        current_locale = get_locale()

        # Define the background task
        def run_prepare():
            set_locale(current_locale)
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message=t('progress.startPreparingEnv')
                )
                
                # Prepare the simulation, reporting progress
                # Per-stage progress detail
                stage_details = {}
                
                def progress_callback(stage, progress, message, **kwargs):
                    # Compute the overall progress
                    stage_weights = {
                        "reading": (0, 20),           # 0-20%
                        "generating_profiles": (20, 70),  # 20-70%
                        "generating_config": (70, 90),    # 70-90%
                        "copying_scripts": (90, 100)       # 90-100%
                    }
                    
                    start, end = stage_weights.get(stage, (0, 100))
                    current_progress = int(start + (end - start) * progress / 100)
                    
                    # Build the detailed progress payload
                    stage_names = {
                        "reading": t('progress.readingGraphEntities'),
                        "generating_profiles": t('progress.generatingProfiles'),
                        "generating_config": t('progress.generatingSimConfig'),
                        "copying_scripts": t('progress.preparingScripts')
                    }
                    
                    stage_index = list(stage_weights.keys()).index(stage) + 1 if stage in stage_weights else 1
                    total_stages = len(stage_weights)
                    
                    # Update the stage detail
                    stage_details[stage] = {
                        "stage_name": stage_names.get(stage, stage),
                        "stage_progress": progress,
                        "current": kwargs.get("current", 0),
                        "total": kwargs.get("total", 0),
                        "item_name": kwargs.get("item_name", "")
                    }
                    
                    # Build the detailed progress payload
                    detail = stage_details[stage]
                    progress_detail_data = {
                        "current_stage": stage,
                        "current_stage_name": stage_names.get(stage, stage),
                        "stage_index": stage_index,
                        "total_stages": total_stages,
                        "stage_progress": progress,
                        "current_item": detail["current"],
                        "total_items": detail["total"],
                        "item_description": message
                    }
                    
                    # Build the short message
                    if detail["total"] > 0:
                        detailed_message = (
                            f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: "
                            f"{detail['current']}/{detail['total']} - {message}"
                        )
                    else:
                        detailed_message = f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: {message}"
                    
                    task_manager.update_task(
                        task_id,
                        progress=current_progress,
                        message=detailed_message,
                        progress_detail=progress_detail_data
                    )
                
                result_state = manager.prepare_simulation(
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement,
                    document_text=document_text,
                    defined_entity_types=entity_types_list,
                    use_llm_for_profiles=use_llm_for_profiles,
                    progress_callback=progress_callback,
                    parallel_profile_count=parallel_profile_count,
                    max_agents=max_agents
                )

                if result_state.status == SimulationStatus.FAILED:
                    task_manager.fail_task(
                        task_id,
                        result_state.error or "simulation preparation failed"
                    )
                else:
                    task_manager.complete_task(
                        task_id,
                        result=result_state.to_simple_dict()
                    )
                
            except Exception as e:
                logger.error(f"simulation preparation failed: {str(e)}")
                task_manager.fail_task(task_id, str(e))
                
                # Mark the simulation as failed
                state = manager.get_simulation(simulation_id)
                if state:
                    state.status = SimulationStatus.FAILED
                    state.error = str(e)
                    manager._save_simulation_state(state)
        
        # Start the background thread
        thread = threading.Thread(target=run_prepare, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "task_id": task_id,
                "status": "preparing",
                "message": t('api.prepareStarted'),
                "already_prepared": False,
                "expected_entities_count": state.entities_count,  # Segments read from the graph
                "expected_agent_count": expected_agent_count,     # Cast size after planning
                "max_agents": max_agents,
                "entity_types": state.entity_types  # The entity types
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.exception(f"failed to start preparation task: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/prepare/status', methods=['POST'])
def get_prepare_status():
    """
    Query the progress of a preparation task.
    
    Two ways to query:
    1. By task_id, for a task in flight
    2. By simulation_id, to check for preparation that already finished
    
    Request (JSON):
        {
            "task_id": "task_xxxx",          // optional, the task_id prepare returned
            "simulation_id": "sim_xxxx"      // optional, to check for finished preparation
        }
    
    Returns:
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|ready",
                "progress": 45,
                "message": "...",
                "already_prepared": true|false,  // whether preparation had already finished
                "prepare_info": {...}            // detail, present once preparation finished
            }
        }
    """
    from ..models.task import TaskManager
    
    try:
        data = request.get_json() or {}
        
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')
        
        # With a simulation_id, check first whether preparation already finished
        if simulation_id:
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            if is_prepared:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "progress": 100,
                        "message": t('api.alreadyPrepared'),
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
        
        # No task_id: return an error
        if not task_id:
            if simulation_id:
                # A simulation_id was given, but preparation has not finished
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "not_started",
                        "progress": 0,
                        "message": t('api.notStartedPrepare'),
                        "already_prepared": False
                    }
                })
            return jsonify({
                "success": False,
                "error": t('api.requireTaskOrSimId')
            }), 400
        
        task_manager = TaskManager()
        task = task_manager.get_task(task_id)
        
        if not task:
            # No such task; with a simulation_id, check for finished preparation
            if simulation_id:
                is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
                if is_prepared:
                    return jsonify({
                        "success": True,
                        "data": {
                            "simulation_id": simulation_id,
                            "task_id": task_id,
                            "status": "ready",
                            "progress": 100,
                            "message": t('api.taskCompletedPrepared'),
                            "already_prepared": True,
                            "prepare_info": prepare_info
                        }
                    })
            
            return jsonify({
                "success": False,
                "error": t('api.taskNotFound', id=task_id)
            }), 404
        
        task_dict = task.to_dict()
        task_dict["already_prepared"] = False
        
        return jsonify({
            "success": True,
            "data": task_dict
        })
        
    except Exception as e:
        logger.error(f"failed to query task status: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@simulation_bp.route('/<simulation_id>', methods=['GET'])
def get_simulation(simulation_id: str):
    """Return the simulation state."""
    try:
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        result = state.to_dict()
        
        # Attach the run instructions once the simulation is prepared
        if state.status == SimulationStatus.READY:
            result["run_instructions"] = manager.get_run_instructions(simulation_id)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch simulation status: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/list', methods=['GET'])
def list_simulations():
    """
    List every simulation.
    
    Query parameters:
        project_id: filter by project (optional)
    """
    try:
        project_id = request.args.get('project_id')
        
        manager = SimulationManager()
        simulations = manager.list_simulations(project_id=project_id)
        
        return jsonify({
            "success": True,
            "data": [s.to_dict() for s in simulations],
            "count": len(simulations)
        })
        
    except Exception as e:
        logger.exception(f"failed to list simulations: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/profiles', methods=['GET'])
def get_simulation_profiles(simulation_id: str):
    """
    Return the agent profiles for a simulation.
    
    Query parameters:
        platform: reddit or twitter (default reddit)
    """
    try:
        platform = request.args.get('platform') or _get_default_platform(simulation_id)

        manager = SimulationManager()
        profiles = manager.get_profiles(simulation_id, platform=platform)
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "count": len(profiles),
                "profiles": profiles
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.exception(f"failed to fetch profiles: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/profiles/realtime', methods=['GET'])
def get_simulation_profiles_realtime(simulation_id: str):
    """
    Return the agent profiles live, so progress can be watched during generation.
    
    How this differs from /profiles:
    - Reads the file directly, bypassing SimulationManager
    - Suited to watching generation as it happens
    - Returns extra metadata (file mtime, whether generation is running, ...)
    
    Query parameters:
        platform: reddit or twitter (default reddit)
    
    Returns:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "platform": "reddit",
                "count": 15,
                "total_expected": 93,  // expected total, when known
                "is_generating": true,  // whether generation is running
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "profiles": [...]
            }
        }
    """
    import json
    import csv
    from datetime import datetime
    
    try:
        platform = request.args.get('platform') or _get_default_platform(simulation_id)

        # Locate the simulation directory
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        # Work out the file path
        if platform == "reddit":
            profiles_file = os.path.join(sim_dir, "reddit_profiles.json")
        else:
            profiles_file = os.path.join(sim_dir, "twitter_profiles.csv")
        
        # Does the file exist?
        file_exists = os.path.exists(profiles_file)
        profiles = []
        file_modified_at = None
        
        if file_exists:
            # Read the file mtime
            file_stat = os.stat(profiles_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                if platform == "reddit":
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        profiles = json.load(f)
                else:
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        profiles = list(reader)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"failed to read the profiles file (it may be mid-write): {e}")
                profiles = []
        
        # Is generation running? state.json says so
        is_generating = False
        total_expected = None
        status = None
        error = None
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    total_expected = state_data.get("entities_count")
                    error = state_data.get("error")
            except Exception:
                pass
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "platform": platform,
                "count": len(profiles),
                "total_expected": total_expected,
                "is_generating": is_generating,
                "status": status,
                "error": error,
                "file_exists": file_exists,
                "file_modified_at": file_modified_at,
                "profiles": profiles
            }
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch live profiles: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/config/realtime', methods=['GET'])
def get_simulation_config_realtime(simulation_id: str):
    """
    Return the simulation config live, so progress can be watched during generation.
    
    How this differs from /config:
    - Reads the file directly, bypassing SimulationManager
    - Suited to watching generation as it happens
    - Returns extra metadata (file mtime, whether generation is running, ...)
    - Returns partial information even before generation finishes
    
    Returns:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "is_generating": true,  // whether generation is running
                "generation_stage": "generating_config",  // the current generation stage
                "config": {...}  // the config, when it exists
            }
        }
    """
    import json
    from datetime import datetime
    
    try:
        # Locate the simulation directory
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        # Config file path
        config_file = os.path.join(sim_dir, "simulation_config.json")
        
        # Does the file exist?
        file_exists = os.path.exists(config_file)
        config = None
        file_modified_at = None
        
        if file_exists:
            # Read the file mtime
            file_stat = os.stat(config_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"failed to read the config file (it may be mid-write): {e}")
                config = None
        
        # Is generation running? state.json says so
        is_generating = False
        generation_stage = None
        status = None
        error = None
        profiles_generated = False
        config_generated = False
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    error = state_data.get("error")
                    is_generating = status == "preparing"
                    profiles_generated = state_data.get("profiles_generated", False)
                    config_generated = state_data.get("config_generated", False)
                    
                    # Work out the current stage
                    if is_generating:
                        if profiles_generated:
                            generation_stage = "generating_config"
                        else:
                            generation_stage = "generating_profiles"
                    elif status == "ready":
                        generation_stage = "completed"
                    elif status == "failed":
                        generation_stage = "failed"
            except Exception:
                pass
        
        # Build the response
        response_data = {
            "simulation_id": simulation_id,
            "file_exists": file_exists,
            "file_modified_at": file_modified_at,
            "is_generating": is_generating,
            "status": status,
            "error": error,
            "generation_stage": generation_stage,
            "profiles_generated": profiles_generated,
            "config_generated": config_generated,
            "config": config
        }
        
        # When the config exists, pull out a few key statistics
        if config:
            response_data["summary"] = {
                "total_agents": len(config.get("agent_configs", [])),
                "simulation_hours": config.get("time_config", {}).get("total_simulation_hours"),
                "initial_posts_count": len(config.get("event_config", {}).get("initial_posts", [])),
                "hot_topics_count": len(config.get("event_config", {}).get("hot_topics", [])),
                "has_twitter_config": "twitter_config" in config,
                "has_reddit_config": "reddit_config" in config,
                "generated_at": config.get("generated_at"),
                "llm_model": config.get("llm_model")
            }
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch live config: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/config', methods=['GET'])
def get_simulation_config(simulation_id: str):
    """
    Return the simulation config, as generated in full by the LLM.
    
    Contains:
        - time_config: timing (duration, rounds, peak and trough bands)
        - agent_configs: per-agent activity (activity level, posting frequency, stance, ...)
        - event_config: events (initial posts, hot topics)
        - platform_configs: platform settings
        - generation_reasoning: the LLM's reasoning for this config
    """
    try:
        manager = SimulationManager()
        config = manager.get_simulation_config(simulation_id)
        
        if not config:
            return jsonify({
                "success": False,
                "error": t('api.configNotFound')
            }), 404
        
        return jsonify({
            "success": True,
            "data": config
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch config: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/config/download', methods=['GET'])
def download_simulation_config(simulation_id: str):
    """Download the simulation config file."""
    try:
        manager = SimulationManager()
        sim_dir = manager._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return jsonify({
                "success": False,
                "error": t('api.configFileNotFound')
            }), 404
        
        return send_file(
            config_path,
            as_attachment=True,
            download_name="simulation_config.json"
        )
        
    except Exception as e:
        logger.exception(f"failed to download config: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/script/<script_name>/download', methods=['GET'])
def download_simulation_script(script_name: str):
    """
    Download a simulation runner script from backend/scripts/.
    
    Valid script_name values:
        - run_parallel_simulation.py
        - action_logger.py
    """
    try:
        # The scripts live in backend/scripts/
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        # Validate the script name
        allowed_scripts = [
            "run_parallel_simulation.py",
            "action_logger.py"
        ]
        
        if script_name not in allowed_scripts:
            return jsonify({
                "success": False,
                "error": t('api.unknownScript', name=script_name, allowed=allowed_scripts)
            }), 400
        
        script_path = os.path.join(scripts_dir, script_name)
        
        if not os.path.exists(script_path):
            return jsonify({
                "success": False,
                "error": t('api.scriptFileNotFound', name=script_name)
            }), 404
        
        return send_file(
            script_path,
            as_attachment=True,
            download_name=script_name
        )
        
    except Exception as e:
        logger.exception(f"failed to download script: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


# ============== Standalone profile generation endpoints ==============

@simulation_bp.route('/generate-profiles', methods=['POST'])
def generate_profiles():
    """
    Generate OASIS agent profiles straight from a graph, without a simulation.
    
    Request (JSON):
        {
            "graph_id": "spiegel_xxxx",     // required
            "entity_types": ["Student"],      // optional
            "use_llm": true,                  // optional
            "platform": "reddit",             // optional
            "max_agents": 500                 // optional, capped at MAX_AGENTS.
                                              // Defaults to the entity count,
                                              // so no entity is cloned here.
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        if not graph_id:
            return jsonify({
                "success": False,
                "error": t('api.requireGraphId')
            }), 400
        
        entity_types = data.get('entity_types')
        use_llm = data.get('use_llm', True)
        platform = data.get('platform', 'reddit')
        
        reader = ZepEntityReader()
        filtered = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=True
        )
        
        if filtered.filtered_count == 0:
            return jsonify({
                "success": False,
                "error": t('api.noMatchingEntities')
            }), 400
        
        # Standalone generation is a preview of the entities themselves, so it
        # does not clone by default - it only applies the cap. Pass max_agents
        # to plan a full cast here as the simulation pipeline does.
        max_agents = int(data.get('max_agents') or min(MAX_AGENTS, filtered.filtered_count))

        generator = OasisProfileGenerator()
        profiles = generator.generate_profiles_from_entities(
            entities=filtered.entities,
            use_llm=use_llm,
            max_agents=min(max_agents, MAX_AGENTS)
        )
        
        if platform == "reddit":
            profiles_data = [p.to_reddit_format() for p in profiles]
        elif platform == "twitter":
            profiles_data = [p.to_twitter_format() for p in profiles]
        else:
            profiles_data = [p.to_dict() for p in profiles]
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "entity_types": list(filtered.entity_types),
                "count": len(profiles_data),
                "profiles": profiles_data
            }
        })
        
    except Exception as e:
        logger.exception(f"failed to generate profiles: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


# ============== Simulation run control endpoints ==============

@simulation_bp.route('/start', methods=['POST'])
def start_simulation():
    """
    Start the simulation.

    Request (JSON):
        {
            "simulation_id": "sim_xxxx",          // required, the simulation ID
            "platform": "parallel",                // optional: twitter / reddit / parallel (default)
            "max_rounds": 100,                     // optional: round cap, truncates an over-long run
            "enable_graph_memory_update": false,   // optional: stream agent activity into the Zep graph
            "force": false                         // optional: force a restart (stops the run and clears the logs)
        }

    About force:
        - When set, a running or finished simulation is stopped and its run logs cleared
        - Cleared: run_state.json, actions.jsonl, simulation.log and similar
        - The config file (simulation_config.json) and the profile files are kept
        - Use it when the simulation needs to be run again

    About enable_graph_memory_update:
        - When set, every agent action (posts, comments, likes, ...) is streamed into the Zep graph
        - That lets the graph remember the run, for later analysis or AI chat
        - The linked project must have a valid graph_id
        - Updates are batched, to keep the API call count down

    Returns:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "process_pid": 12345,
                "twitter_running": true,
                "reddit_running": true,
                "started_at": "2025-12-01T10:00:00",
                "graph_memory_update_enabled": true,  // whether graph memory updates are on
                "force_restarted": true               // whether this was a forced restart
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        platform = data.get('platform', 'parallel')
        max_rounds = data.get('max_rounds')  # Optional: round cap
        enable_graph_memory_update = data.get('enable_graph_memory_update', False)  # Optional: graph memory updates
        force = data.get('force', False)  # Optional: force a restart
        if not isinstance(enable_graph_memory_update, bool):
            return jsonify({
                "success": False,
                "error": "enable_graph_memory_update must be a JSON boolean",
            }), 400
        if not isinstance(force, bool):
            return jsonify({
                "success": False,
                "error": "force must be a JSON boolean",
            }), 400

        # Validate max_rounds
        if max_rounds is not None:
            try:
                max_rounds = int(max_rounds)
                if max_rounds <= 0:
                    return jsonify({
                        "success": False,
                        "error": t('api.maxRoundsPositive')
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    "success": False,
                    "error": t('api.maxRoundsInvalid')
                }), 400

        if platform not in ['twitter', 'reddit', 'parallel']:
            return jsonify({
                "success": False,
                "error": t('api.invalidPlatform', platform=platform)
            }), 400

        # Is the simulation prepared?
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404

        force_restarted = False
        
        # Status handling: allow a restart once preparation has finished
        if state.status != SimulationStatus.READY:
            # Has preparation finished?
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)

            if is_prepared:
                run_state = SimulationRunner.get_run_state(simulation_id)
                updater = ZepGraphMemoryManager.get_updater(simulation_id)
                needs_finalization = bool(
                    run_state
                    and run_state.runner_status in {
                        RunnerStatus.RUNNING,
                        RunnerStatus.PAUSED,
                        RunnerStatus.STOPPING,
                        RunnerStatus.FAILED,
                    }
                    and (
                        run_state.runner_status
                        in {
                            RunnerStatus.RUNNING,
                            RunnerStatus.PAUSED,
                            RunnerStatus.STOPPING,
                        }
                        or updater is not None
                    )
                )
                if needs_finalization:
                    if not force:
                        return jsonify({
                            "success": False,
                            "error": t('api.simRunningForceHint')
                        }), 400
                    logger.info(f"force mode: terminating the previous simulation first {simulation_id}")
                    try:
                        stopped = SimulationRunner.stop_simulation(simulation_id)
                    except SimulationStopPending as error:
                        return jsonify({
                            "success": False,
                            "pending": True,
                            "error": str(error),
                        }), 409
                    except Exception as error:
                        return jsonify({
                            "success": False,
                            "error": (
                                "Cannot restart until the previous simulation "
                                f"finalizes safely: {error}"
                            ),
                        }), 409
                    if stopped.runner_status != RunnerStatus.STOPPED:
                        return jsonify({
                            "success": False,
                            "error": "Previous simulation did not reach STOPPED",
                        }), 409

                # Forced restart: clear the run logs
                if force:
                    logger.info(f"force mode: cleaning simulation logs {simulation_id}")
                    cleanup_result = SimulationRunner.cleanup_simulation_logs(simulation_id)
                    if not cleanup_result.get("success"):
                        return jsonify({
                            "success": False,
                            "error": (
                                "Failed to clean previous simulation logs: "
                                f"{cleanup_result.get('errors')}"
                            ),
                        }), 500
                    force_restarted = True

                # The process is gone or finished: reset the status to ready
                logger.info(f"simulation {simulation_id} preparation finished; resetting status to ready (was: {state.status.value})")
                state.status = SimulationStatus.READY
                manager._save_simulation_state(state)
            else:
                # Preparation has not finished
                return jsonify({
                    "success": False,
                    "error": t('api.simNotReady', status=state.status.value)
                }), 400
        
        # Read the graph ID, used for graph memory updates
        graph_id = None
        if enable_graph_memory_update:
            # The project is authoritative. A graph ID copied into an older
            # simulation can outlive a project reset/rebuild and must not be
            # used to resurrect writes to a deleted graph.
            project = ProjectManager.get_project(state.project_id)
            graph_id = project.graph_id if project else None
            if not graph_id:
                return jsonify({
                    "success": False,
                    "error": t('api.graphIdRequiredForMemory')
                }), 400

        graph_guard = (
            graph_lifecycle_lock(graph_id)
            if enable_graph_memory_update
            else nullcontext()
        )
        with graph_guard:
            if enable_graph_memory_update:
                # Re-read both references under the same per-graph lock used
                # by reset/delete. Keep the lock through updater creation in
                # start_simulation so check -> claim is atomic.
                refreshed_state = manager.get_simulation(simulation_id)
                refreshed_project = (
                    ProjectManager.get_project(refreshed_state.project_id)
                    if refreshed_state
                    else None
                )
                current_graph_id = (
                    refreshed_project.graph_id if refreshed_project else None
                )
                if current_graph_id != graph_id:
                    return jsonify({
                        "success": False,
                        "error": (
                            "The project graph changed while the simulation "
                            "was starting; retry after refreshing the project"
                        ),
                    }), 409
                if (
                    refreshed_state.graph_id
                    and refreshed_state.graph_id != current_graph_id
                ):
                    return jsonify({
                        "success": False,
                        "error": (
                            "The simulation references an older graph; "
                            "prepare it again before enabling graph memory"
                        ),
                    }), 409
                active_reports = get_graph_readers(graph_id)
                if active_reports:
                    return jsonify({
                        "success": False,
                        "error": (
                            "A report is currently reading this graph; wait "
                            "for report generation to finish before enabling "
                            "graph memory updates"
                        ),
                        "active_reports": active_reports,
                    }), 409
                state = refreshed_state
                logger.info(
                    "enabling graph memory updates: simulation_id=%s, graph_id=%s",
                    simulation_id,
                    graph_id,
                )

            # Start the simulation. With graph writes enabled, graph_guard is
            # held until the updater claim and the process resources are all released.
            run_state = SimulationRunner.start_simulation(
                simulation_id=simulation_id,
                platform=platform,
                max_rounds=max_rounds,
                enable_graph_memory_update=enable_graph_memory_update,
                graph_id=graph_id
            )
        
        response_data = run_state.to_dict()
        if max_rounds:
            response_data['max_rounds_applied'] = max_rounds
        response_data['graph_memory_update_enabled'] = enable_graph_memory_update
        response_data['force_restarted'] = force_restarted
        if enable_graph_memory_update:
            response_data['graph_id'] = graph_id
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.exception(f"failed to start simulation: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/stop', methods=['POST'])
def stop_simulation():
    """
    Stop the simulation.
    
    Request (JSON):
        {
            "simulation_id": "sim_xxxx"  // required, the simulation ID
        }
    
    Returns:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "stopped",
                "completed_at": "2025-12-01T12:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        run_state = SimulationRunner.stop_simulation(simulation_id)
        
        # Update the simulation state
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.STOPPED
            state.error = None
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })

    except SimulationStopPending as e:
        return jsonify({
            "success": False,
            "pending": True,
            "error": str(e),
        }), 202

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"failed to stop simulation: {str(e)}")
        simulation_id = (request.get_json(silent=True) or {}).get('simulation_id')
        if simulation_id:
            manager = SimulationManager()
            state = manager.get_simulation(simulation_id)
            if state:
                state.status = SimulationStatus.FAILED
                state.error = str(e)
                manager._save_simulation_state(state)
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


# ============== Live status endpoints ==============

@simulation_bp.route('/<simulation_id>/run-status', methods=['GET'])
def get_run_status(simulation_id: str):
    """
    Return the live run status, for the frontend to poll.
    
    Returns:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                "total_rounds": 144,
                "progress_percent": 3.5,
                "simulated_hours": 2,
                "total_simulation_hours": 72,
                "twitter_running": true,
                "reddit_running": true,
                "twitter_actions_count": 150,
                "reddit_actions_count": 200,
                "total_actions_count": 350,
                "started_at": "2025-12-01T10:00:00",
                "updated_at": "2025-12-01T10:30:00"
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "current_round": 0,
                    "total_rounds": 0,
                    "progress_percent": 0,
                    "twitter_actions_count": 0,
                    "reddit_actions_count": 0,
                    "total_actions_count": 0,
                }
            })
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch run status: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/run-status/detail', methods=['GET'])
def get_run_status_detail(simulation_id: str):
    """
    Return the detailed run status, including every action.
    
    Powers the live activity view in the frontend.
    
    Query parameters:
        platform: filter by platform (twitter/reddit, optional)
    
    Returns:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                ...
                "all_actions": [
                    {
                        "round_num": 5,
                        "timestamp": "2025-12-01T10:30:00",
                        "platform": "twitter",
                        "agent_id": 3,
                        "agent_name": "Agent Name",
                        "action_type": "CREATE_POST",
                        "action_args": {"content": "..."},
                        "result": null,
                        "success": true
                    },
                    ...
                ],
                "twitter_actions": [...],  # every action on Twitter
                "reddit_actions": [...]    # every action on Reddit
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        platform_filter = request.args.get('platform')
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "all_actions": [],
                    "twitter_actions": [],
                    "reddit_actions": []
                }
            })
        
        # Load the full action list
        all_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter
        )
        
        # Split the actions per platform
        twitter_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="twitter"
        ) if not platform_filter or platform_filter == "twitter" else []
        
        reddit_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="reddit"
        ) if not platform_filter or platform_filter == "reddit" else []
        
        # Actions for the current round (recent_actions shows the latest round only)
        current_round = run_state.current_round
        recent_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter,
            round_num=current_round
        ) if current_round > 0 else []
        
        # Read the base status
        result = run_state.to_dict()
        result["all_actions"] = [a.to_dict() for a in all_actions]
        result["twitter_actions"] = [a.to_dict() for a in twitter_actions]
        result["reddit_actions"] = [a.to_dict() for a in reddit_actions]
        result["rounds_count"] = len(run_state.rounds)
        # recent_actions shows only the latest round, across both platforms
        result["recent_actions"] = [a.to_dict() for a in recent_actions]
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch detailed status: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/actions', methods=['GET'])
def get_simulation_actions(simulation_id: str):
    """
    Return the agent action history for a simulation.
    
    Query parameters:
        limit: page size (default 100)
        offset: page offset (default 0)
        platform: filter by platform (twitter/reddit)
        agent_id: filter by agent ID
        round_num: filter by round
    
    Returns:
        {
            "success": true,
            "data": {
                "count": 100,
                "actions": [...]
            }
        }
    """
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        platform = request.args.get('platform')
        agent_id = request.args.get('agent_id', type=int)
        round_num = request.args.get('round_num', type=int)
        
        actions = SimulationRunner.get_actions(
            simulation_id=simulation_id,
            limit=limit,
            offset=offset,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(actions),
                "actions": [a.to_dict() for a in actions]
            }
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch action history: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/timeline', methods=['GET'])
def get_simulation_timeline(simulation_id: str):
    """
    Return the simulation timeline, aggregated per round.
    
    Powers the progress bar and timeline view in the frontend.
    
    Query parameters:
        start_round: first round (default 0)
        end_round: last round (default all)
    
    Returns the per-round summary.
    """
    try:
        start_round = request.args.get('start_round', 0, type=int)
        end_round = request.args.get('end_round', type=int)
        
        timeline = SimulationRunner.get_timeline(
            simulation_id=simulation_id,
            start_round=start_round,
            end_round=end_round
        )
        
        return jsonify({
            "success": True,
            "data": {
                "rounds_count": len(timeline),
                "timeline": timeline
            }
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch timeline: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/campaign-metrics', methods=['GET'])
def get_campaign_metrics(simulation_id: str):
    """
    Return the measured marketing KPIs for the campaign.

    Reach, engagement, virality, sentiment split, share of voice and the
    per-segment breakdown, all counted from the action log rather than
    estimated. Powers the campaign KPI dashboard, and is the same data the
    report agent quotes.
    """
    try:
        from ..services.campaign_metrics import CampaignMetricsService

        metrics = CampaignMetricsService.compute_as_dict(simulation_id)

        return jsonify({
            "success": True,
            "data": metrics
        })

    except Exception as e:
        logger.exception(f"failed to compute campaign KPIs: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/sentiment-digest', methods=['GET'])
def get_sentiment_digest(simulation_id: str):
    """
    Return the sentiment digest for what the audience actually wrote.

    The counted KPIs read sentiment off the action type - a like is approval, a
    dislike is rejection. This endpoint classifies the text of every authored
    post and comment instead, and returns:

    - the positive / neutral / negative split, for posts and comments
    - the most-liked post and comment on each side
    - the objections that recur among the negatives
    - the hooks that recur among the positives

    The classification is an LLM pass, cached per simulation and reused until
    the feed grows. Pass ?force=true to reclassify.

    Query parameters:
        force: true to reclassify even when a cached digest matches
    """
    try:
        from ..services.content_sentiment import ContentSentimentService

        force = request.args.get('force', 'false').lower() in ('true', '1', 'yes')

        # The campaign brief keeps the model judging sentiment toward THIS
        # campaign rather than in the abstract.
        campaign_requirement = ""
        state = SimulationManager().get_simulation(simulation_id)
        if state:
            project = ProjectManager.get_project(state.project_id)
            if project:
                campaign_requirement = project.simulation_requirement or ""

        digest = ContentSentimentService().compute(
            simulation_id=simulation_id,
            campaign_requirement=campaign_requirement,
            force=force
        )

        return jsonify({
            "success": True,
            "data": digest
        })

    except Exception as e:
        logger.exception(f"failed to generate sentiment digest: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/agent-stats', methods=['GET'])
def get_agent_stats(simulation_id: str):
    """
    Return per-agent statistics.
    
    Powers the agent activity leaderboard and action distribution views.
    """
    try:
        stats = SimulationRunner.get_agent_stats(simulation_id)
        
        return jsonify({
            "success": True,
            "data": {
                "agents_count": len(stats),
                "stats": stats
            }
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch agent statistics: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


# ============== Database query endpoints ==============

@simulation_bp.route('/<simulation_id>/posts', methods=['GET'])
def get_simulation_posts(simulation_id: str):
    """
    Return the posts from a simulation.
    
    Query parameters:
        platform: twitter or reddit
        limit: page size (default 50)
        offset: page offset
        sort_by: created_at (default), num_likes, num_dislikes, num_shares,
                 num_comments or post_id
        order: desc (default) or asc

    Returns the posts, read from the SQLite database. Each post carries its
    author (joined from the user table) and its comment count (counted from the
    comment table), so the feed board can sort on either without a second
    request.
    """
    try:
        platform = request.args.get('platform') or _get_default_platform(simulation_id)
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        # Whitelist the sort column: it is interpolated into the SQL, so it can
        # never come straight from the query string.
        sort_by = request.args.get('sort_by', 'created_at')
        if sort_by not in POST_SORT_COLUMNS:
            sort_by = 'created_at'
        sort_column = POST_SORT_COLUMNS[sort_by]

        order = 'ASC' if request.args.get('order', 'desc').lower() == 'asc' else 'DESC'

        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )

        db_file = f"{platform}_simulation.db"
        db_path = os.path.join(sim_dir, db_file)
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "platform": platform,
                    "count": 0,
                    "posts": [],
                    "message": t('api.dbNotExist')
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            # num_comments is aggregated here rather than in a second query so
            # it can be sorted on. The author join lets the board show the
            # persona name instead of a bare user_id.
            # OASIS stores a repost as a row with content NULL pointing at the
            # original, so the original is joined in here. Without it the board
            # shows a screenful of blank rows - which is what a repost looks
            # like when only its own content is read.
            cursor.execute(f"""
                SELECT
                    p.post_id, p.user_id, p.original_post_id, p.content,
                    p.quote_content, p.created_at, p.num_likes, p.num_dislikes,
                    p.num_shares, p.num_reports,
                    u.user_name AS author_user_name,
                    u.name AS author_name,
                    o.content AS original_content,
                    ou.user_name AS original_author_user_name,
                    ou.name AS original_author_name,
                    COUNT(c.comment_id) AS num_comments
                FROM post p
                LEFT JOIN user u ON u.user_id = p.user_id
                LEFT JOIN post o ON o.post_id = p.original_post_id
                LEFT JOIN user ou ON ou.user_id = o.user_id
                LEFT JOIN comment c ON c.post_id = p.post_id
                GROUP BY p.post_id
                ORDER BY {sort_column} {order}
                LIMIT ? OFFSET ?
            """, (limit, offset))

            posts = [dict(row) for row in cursor.fetchall()]

            cursor.execute("SELECT COUNT(*) FROM post")
            total = cursor.fetchone()[0]

        except sqlite3.OperationalError:
            posts = []
            total = 0

        conn.close()

        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "total": total,
                "count": len(posts),
                "sort_by": sort_by,
                "order": order.lower(),
                "posts": posts
            }
        })

    except Exception as e:
        logger.exception(f"failed to fetch posts: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/<simulation_id>/comments', methods=['GET'])
def get_simulation_comments(simulation_id: str):
    """
    Return the comments from a simulation.

    Query parameters:
        platform: twitter or reddit (chosen from the simulation config by default)
        post_id: filter by post ID (optional)
        limit: page size
        offset: page offset
    """
    try:
        platform = request.args.get('platform') or _get_default_platform(simulation_id)
        post_id = request.args.get('post_id')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_path = os.path.join(sim_dir, f"{platform}_simulation.db")
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "count": 0,
                    "comments": []
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            # The author join matches the posts endpoint, so a comment thread
            # shows persona names rather than bare user_ids.
            base_query = """
                SELECT
                    c.comment_id, c.post_id, c.user_id, c.content,
                    c.created_at, c.num_likes, c.num_dislikes,
                    u.user_name AS author_user_name,
                    u.name AS author_name
                FROM comment c
                LEFT JOIN user u ON u.user_id = c.user_id
            """

            if post_id:
                cursor.execute(base_query + """
                    WHERE c.post_id = ?
                    ORDER BY c.num_likes DESC, c.created_at ASC
                    LIMIT ? OFFSET ?
                """, (post_id, limit, offset))
            else:
                cursor.execute(base_query + """
                    ORDER BY c.created_at DESC
                    LIMIT ? OFFSET ?
                """, (limit, offset))

            comments = [dict(row) for row in cursor.fetchall()]

        except sqlite3.OperationalError:
            comments = []
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(comments),
                "comments": comments
            }
        })
        
    except Exception as e:
        logger.exception(f"failed to fetch comments: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


# ============== Interview endpoints ==============

@simulation_bp.route('/interview', methods=['POST'])
def interview_agent():
    """
    Interview a single agent.

    Note: the simulation environment must be running - that is, past the
    simulation loop and waiting for commands.

    Request (JSON):
        {
            "simulation_id": "sim_xxxx",       // required, the simulation ID
            "agent_id": 0,                     // required, the agent ID
            "prompt": "what do you make of this?",  // required, the interview question
            "platform": "twitter",             // optional, target platform (twitter/reddit)
                                               // omitted: a dual-platform run interviews both
            "timeout": 60                      // optional, timeout in seconds, default 60
        }

    Returns (no platform given - dual-platform mode):
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "what do you make of this?",
                "result": {
                    "agent_id": 0,
                    "prompt": "...",
                    "platforms": {
                        "twitter": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit": {"agent_id": 0, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }

    Returns (platform given):
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "what do you make of this?",
                "result": {
                    "agent_id": 0,
                    "response": "I think...",
                    "platform": "twitter",
                    "timestamp": "2025-12-08T10:00:00"
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        agent_id = data.get('agent_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # Optional: twitter / reddit / None
        timeout = data.get('timeout', 60)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        if agent_id is None:
            return jsonify({
                "success": False,
                "error": t('api.requireAgentId')
            }), 400
        
        if not prompt:
            return jsonify({
                "success": False,
                "error": t('api.requirePrompt')
            }), 400
        
        # Validate the platform
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": t('api.invalidInterviewPlatform')
            }), 400
        
        # Check the environment status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": t('api.envNotRunning')
            }), 400
        
        # Prefix the prompt so the agent does not reach for a tool
        optimized_prompt = optimize_interview_prompt(prompt)
        
        result = SimulationRunner.interview_agent(
            simulation_id=simulation_id,
            agent_id=agent_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": t('api.interviewTimeout', error=str(e))
        }), 504
        
    except Exception as e:
        logger.exception(f"interview failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/interview/batch', methods=['POST'])
def interview_agents_batch():
    """
    Interview several agents in one batch.

    Note: the simulation environment must be running.

    Request (JSON):
        {
            "simulation_id": "sim_xxxx",       // required, the simulation ID
            "interviews": [                    // required, the interview list
                {
                    "agent_id": 0,
                    "prompt": "what do you make of A?",
                    "platform": "twitter"      // optional, the platform for this agent
                },
                {
                    "agent_id": 1,
                    "prompt": "what do you make of B?"  // no platform: the default is used
                }
            ],
            "platform": "reddit",              // optional, default platform (an item's own wins)
                                               // omitted: a dual-platform run interviews every agent on both
            "timeout": 120                     // optional, timeout in seconds, default 120
        }

    Returns:
        {
            "success": true,
            "data": {
                "interviews_count": 2,
                "result": {
                    "interviews_count": 4,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        "twitter_1": {"agent_id": 1, "response": "...", "platform": "twitter"},
                        "reddit_1": {"agent_id": 1, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        interviews = data.get('interviews')
        platform = data.get('platform')  # Optional: twitter / reddit / None
        timeout = data.get('timeout', 120)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        if not interviews or not isinstance(interviews, list):
            return jsonify({
                "success": False,
                "error": t('api.requireInterviews')
            }), 400

        # Validate the platform
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": t('api.invalidInterviewPlatform')
            }), 400

        # Validate each interview item
        for i, interview in enumerate(interviews):
            if 'agent_id' not in interview:
                return jsonify({
                    "success": False,
                    "error": t('api.interviewListMissingAgentId', index=i+1)
                }), 400
            if 'prompt' not in interview:
                return jsonify({
                    "success": False,
                    "error": t('api.interviewListMissingPrompt', index=i+1)
                }), 400
            # Validate the item's own platform, when present
            item_platform = interview.get('platform')
            if item_platform and item_platform not in ("twitter", "reddit"):
                return jsonify({
                    "success": False,
                    "error": t('api.interviewListInvalidPlatform', index=i+1)
                }), 400

        # Check the environment status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": t('api.envNotRunning')
            }), 400

        # Prefix each item's prompt so the agent does not reach for a tool
        optimized_interviews = []
        for interview in interviews:
            optimized_interview = interview.copy()
            optimized_interview['prompt'] = optimize_interview_prompt(interview.get('prompt', ''))
            optimized_interviews.append(optimized_interview)

        result = SimulationRunner.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=optimized_interviews,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": t('api.batchInterviewTimeout', error=str(e))
        }), 504

    except Exception as e:
        logger.exception(f"batch interview failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/interview/all', methods=['POST'])
def interview_all_agents():
    """
    Global interview - ask every agent the same question.

    Note: the simulation environment must be running.

    Request (JSON):
        {
            "simulation_id": "sim_xxxx",            // required, the simulation ID
            "prompt": "what do you make of all this?",  // required, asked of every agent
            "platform": "reddit",                   // optional, target platform (twitter/reddit)
                                                    // omitted: a dual-platform run interviews every agent on both
            "timeout": 180                          // optional, timeout in seconds, default 180
        }

    Returns:
        {
            "success": true,
            "data": {
                "interviews_count": 50,
                "result": {
                    "interviews_count": 100,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        ...
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # Optional: twitter / reddit / None
        timeout = data.get('timeout', 180)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        if not prompt:
            return jsonify({
                "success": False,
                "error": t('api.requirePrompt')
            }), 400

        # Validate the platform
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": t('api.invalidInterviewPlatform')
            }), 400

        # Check the environment status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": t('api.envNotRunning')
            }), 400

        # Prefix the prompt so the agent does not reach for a tool
        optimized_prompt = optimize_interview_prompt(prompt)

        result = SimulationRunner.interview_all_agents(
            simulation_id=simulation_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": t('api.globalInterviewTimeout', error=str(e))
        }), 504

    except Exception as e:
        logger.exception(f"global interview failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/interview/history', methods=['POST'])
def get_interview_history():
    """
    Return the interview history.

    Reads every interview record out of the simulation databases.

    Request (JSON):
        {
            "simulation_id": "sim_xxxx",  // required, the simulation ID
            "platform": "reddit",          // optional, platform (reddit/twitter)
                                           // omitted: history from both platforms
            "agent_id": 0,                 // optional, restrict to one agent
            "limit": 100                   // optional, page size, default 100
        }

    Returns:
        {
            "success": true,
            "data": {
                "count": 10,
                "history": [
                    {
                        "agent_id": 0,
                        "response": "I think...",
                        "prompt": "what do you make of this?",
                        "timestamp": "2025-12-08T10:00:00",
                        "platform": "reddit"
                    },
                    ...
                ]
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        platform = data.get('platform')  # Omitted: history from both platforms
        agent_id = data.get('agent_id')
        limit = data.get('limit', 100)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        history = SimulationRunner.get_interview_history(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            limit=limit
        )

        return jsonify({
            "success": True,
            "data": {
                "count": len(history),
                "history": history
            }
        })

    except Exception as e:
        logger.exception(f"failed to fetch interview history: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/env-status', methods=['POST'])
def get_env_status():
    """
    Return the simulation environment status.

    Checks whether the environment is alive and can take interview commands.

    Request (JSON):
        {
            "simulation_id": "sim_xxxx"  // required, the simulation ID
        }

    Returns:
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "env_alive": true,
                "twitter_available": true,
                "reddit_available": true,
                "message": "the environment is running and can take interview commands"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        env_alive = SimulationRunner.check_env_alive(simulation_id)
        
        # Fetch the more detailed status
        env_status = SimulationRunner.get_env_status_detail(simulation_id)

        if env_alive:
            message = t('api.envRunning')
        else:
            message = t('api.envNotRunningShort')

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "env_alive": env_alive,
                "twitter_available": env_status.get("twitter_available", False),
                "reddit_available": env_status.get("reddit_available", False),
                "message": message
            }
        })

    except Exception as e:
        logger.exception(f"failed to fetch environment status: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500


@simulation_bp.route('/close-env', methods=['POST'])
def close_simulation_env():
    """
    Shut the simulation environment down.
    
    Sends the shutdown command so the simulation leaves command-wait mode
    gracefully.
    
    Note: this is not /stop. /stop kills the process outright, whereas this
    lets the simulation close the environment and exit cleanly.
    
    Request (JSON):
        {
            "simulation_id": "sim_xxxx",  // required, the simulation ID
            "timeout": 30                  // optional, timeout in seconds, default 30
        }
    
    Returns:
        {
            "success": true,
            "data": {
                "message": "shutdown command sent",
                "result": {...},
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        timeout = data.get('timeout', 30)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        result = SimulationRunner.close_simulation_env(
            simulation_id=simulation_id,
            timeout=timeout
        )
        
        # Update the simulation state
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.COMPLETED
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.exception(f"failed to close environment: {str(e)}")
        return jsonify({
            "success": False,
            "error": t('api.internalError')
        }), 500
