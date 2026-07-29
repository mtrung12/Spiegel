"""
OASIS simulation manager.
Drives parallel simulation across the Twitter and Reddit platforms using the
preset scripts plus LLM-generated configuration parameters.
"""

import os
import json
import shutil
import threading
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..models.project import ProjectManager
from ..utils.logger import get_logger
from ..utils.pipeline_logger import pipeline_log
from .agent_population import MAX_AGENTS, derive_population_hints, plan_population
from .zep_entity_reader import ZepEntityReader
from .oasis_profile_generator import OasisProfileGenerator
from .simulation_config_generator import SimulationConfigGenerator
from ..utils.locale import t

logger = get_logger('spiegel.simulation')


class SimulationStatus(str, Enum):
    """Simulation status."""
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    STOPPING = "stopping"
    PAUSED = "paused"
    STOPPED = "stopped"      # Simulation stopped by hand
    COMPLETED = "completed"  # Simulation ran to completion
    FAILED = "failed"


@dataclass
class SimulationState:
    """Simulation status."""
    simulation_id: str
    project_id: str
    graph_id: str
    
    # Which platforms are enabled
    enable_twitter: bool = True
    enable_reddit: bool = True
    
    # Status
    status: SimulationStatus = SimulationStatus.CREATED
    
    # Preparation-stage data
    entities_count: int = 0
    profiles_count: int = 0
    entity_types: List[str] = field(default_factory=list)
    
    # Config generation info
    profiles_generated: bool = False
    config_generated: bool = False
    config_reasoning: str = ""
    
    # Runtime data
    current_round: int = 0
    twitter_status: str = "not_started"
    reddit_status: str = "not_started"
    
    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # Error information
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Full state dict (internal use)."""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "enable_twitter": self.enable_twitter,
            "enable_reddit": self.enable_reddit,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "profiles_generated": self.profiles_generated,
            "config_generated": self.config_generated,
            "config_reasoning": self.config_reasoning,
            "current_round": self.current_round,
            "twitter_status": self.twitter_status,
            "reddit_status": self.reddit_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }
    
    def get_default_platform(self) -> str:
        """Return the default platform given which platforms are enabled."""
        if self.enable_twitter and self.enable_reddit:
            return "reddit"  # Keep the historical default when both are enabled
        elif self.enable_twitter:
            return "twitter"
        else:
            return "reddit"

    def to_simple_dict(self) -> Dict[str, Any]:
        """Trimmed state dict (returned by the API)."""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "profiles_generated": self.profiles_generated,
            "config_generated": self.config_generated,
            "error": self.error,
        }


class SimulationManager:
    """
    Simulation manager.

    Responsibilities:
    1. Read and filter entities from the Zep graph
    2. Generate OASIS agent profiles
    3. Generate the simulation configuration parameters with the LLM
    4. Lay down every file the preset scripts need
    """
    
    # Directory holding simulation data
    SIMULATION_DATA_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../uploads/simulations'
    )

    # Singleton. `_simulations` below is a write-back cache over the state files,
    # and the handlers construct SimulationManager() freely (~20 call sites). As
    # a plain class each of those got its own cache, so two threads serving two
    # requests could hold divergent views of the same simulation and last-writer
    # -wins on disk. One instance per process makes the cache authoritative.
    #
    # Matches TaskManager in models/task.py, and carries the same constraint:
    # the state is per-process, so more than one gunicorn worker would undo it.
    # See the --workers 1 note in docker/entrypoint.sh.
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        # __init__ runs on every SimulationManager() call, including the ones
        # that return the existing instance - so the cache must only be built
        # once, or each construction would silently empty it.
        if self._initialized:
            return

        # Make sure the directory exists
        os.makedirs(self.SIMULATION_DATA_DIR, exist_ok=True)

        # In-memory simulation state cache
        self._simulations: Dict[str, SimulationState] = {}
        # Guards the cache: the handlers are served by gunicorn's thread pool,
        # and the monitor threads in SimulationRunner touch state too.
        self._cache_lock = threading.RLock()
        self._initialized = True

    def _get_simulation_dir(self, simulation_id: str) -> str:
        """Return the data directory for a simulation."""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir
    
    def _save_simulation_state(self, state: SimulationState):
        """Persist the simulation state to disk."""
        sim_dir = self._get_simulation_dir(state.simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        state.updated_at = datetime.now().isoformat()
        
        # Write the file before publishing to the cache, so a reader that wins
        # the lock never sees a cached state with no file behind it.
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)

        with self._cache_lock:
            self._simulations[state.simulation_id] = state

    def _load_simulation_state(self, simulation_id: str) -> Optional[SimulationState]:
        """Load the simulation state from disk."""
        with self._cache_lock:
            cached = self._simulations.get(simulation_id)
        if cached is not None:
            return cached

        sim_dir = self._get_simulation_dir(simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        if not os.path.exists(state_file):
            return None
        
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=data.get("project_id", ""),
            graph_id=data.get("graph_id", ""),
            enable_twitter=data.get("enable_twitter", True),
            enable_reddit=data.get("enable_reddit", True),
            status=SimulationStatus(data.get("status", "created")),
            entities_count=data.get("entities_count", 0),
            profiles_count=data.get("profiles_count", 0),
            entity_types=data.get("entity_types", []),
            profiles_generated=data.get("profiles_generated", False),
            config_generated=data.get("config_generated", False),
            config_reasoning=data.get("config_reasoning", ""),
            current_round=data.get("current_round", 0),
            twitter_status=data.get("twitter_status", "not_started"),
            reddit_status=data.get("reddit_status", "not_started"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            error=data.get("error"),
        )
        
        # Another thread may have loaded the same id concurrently; keep whichever
        # entry is already published so both callers share one object.
        with self._cache_lock:
            return self._simulations.setdefault(simulation_id, state)

    def create_simulation(
        self,
        project_id: str,
        graph_id: str,
        enable_twitter: bool = True,
        enable_reddit: bool = True,
    ) -> SimulationState:
        """
        Create a new simulation.

        Args:
            project_id: Project ID
            graph_id: Zep graph ID
            enable_twitter: Enable the Twitter simulation
            enable_reddit: Enable the Reddit simulation
            
        Returns:
            SimulationState
        """
        import uuid
        simulation_id = f"sim_{uuid.uuid4().hex[:12]}"
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=enable_twitter,
            enable_reddit=enable_reddit,
            status=SimulationStatus.CREATED,
        )
        
        self._save_simulation_state(state)
        logger.info(f"created simulation: {simulation_id}, project={project_id}, graph={graph_id}")
        
        return state
    
    def prepare_simulation(
        self,
        simulation_id: str,
        simulation_requirement: str,
        document_text: str,
        defined_entity_types: Optional[List[str]] = None,
        use_llm_for_profiles: bool = True,
        progress_callback: Optional[callable] = None,
        parallel_profile_count: int = 3,
        max_agents: int = MAX_AGENTS
    ) -> SimulationState:
        """
        Prepare the simulation environment end to end.

        Steps:
        1. Read and filter entities from the Zep graph
        2. Generate an OASIS agent profile per entity (optional LLM enrichment,
           runs in parallel)
        3. Generate the simulation configuration with the LLM (timing, activity
           level, posting frequency, ...)
        4. Write the config and profile files
        5. Copy the preset scripts into the simulation directory

        Args:
            simulation_id: Simulation ID
            simulation_requirement: Simulation requirement (fed to the LLM for config)
            document_text: Source document text (background context for the LLM)
            defined_entity_types: Predefined entity types (optional)
            use_llm_for_profiles: Whether to build detailed personas with the LLM
            progress_callback: Progress callback (stage, progress, message)
            parallel_profile_count: Number of personas generated in parallel, default 3
            max_agents: Cast size. Fewer entities than this are cloned up to it,
                more are truncated to it. Capped at MAX_AGENTS

        Returns:
            SimulationState
        """
        max_agents = max(1, min(int(max_agents or MAX_AGENTS), MAX_AGENTS))
        with pipeline_log.run(
            run_id=simulation_id,
            kind='simulation_prepare',
            requirement_chars=len(simulation_requirement or ''),
            document_chars=len(document_text or ''),
            use_llm_for_profiles=use_llm_for_profiles,
            parallel_profile_count=parallel_profile_count,
            max_agents=max_agents,
        ):
            return self._prepare_simulation_impl(
                simulation_id=simulation_id,
                simulation_requirement=simulation_requirement,
                document_text=document_text,
                defined_entity_types=defined_entity_types,
                use_llm_for_profiles=use_llm_for_profiles,
                progress_callback=progress_callback,
                parallel_profile_count=parallel_profile_count,
                max_agents=max_agents,
            )

    def _prepare_simulation_impl(
        self,
        simulation_id: str,
        simulation_requirement: str,
        document_text: str,
        defined_entity_types: Optional[List[str]] = None,
        use_llm_for_profiles: bool = True,
        progress_callback: Optional[callable] = None,
        parallel_profile_count: int = 3,
        max_agents: int = MAX_AGENTS
    ) -> SimulationState:
        """Run the preparation stages. See prepare_simulation for the contract."""
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"simulation does not exist: {simulation_id}")

        # The stages here are sequential rather than nested, so they use the
        # begin_stage/end_stage pair rather than the `with` form. That pair is
        # not exception-safe on its own: the handler below closes whichever
        # stage was open when the failure hit, so a failed run still gets its
        # stage_end instead of a stage_start with no partner.
        stage_handle = None

        try:
            state.status = SimulationStatus.PREPARING
            state.error = None
            state.profiles_generated = False
            state.config_generated = False
            state.config_reasoning = ""
            self._save_simulation_state(state)
            
            sim_dir = self._get_simulation_dir(simulation_id)
            
            # ========== Stage 1: read and filter entities ==========
            stage_handle = pipeline_log.begin_stage(
                'reading_entities', graph_id=state.graph_id,
            )
            if progress_callback:
                progress_callback("reading", 0, t('progress.connectingZepGraph'))

            reader = ZepEntityReader()

            if progress_callback:
                progress_callback("reading", 30, t('progress.readingNodeData'))

            with pipeline_log.step(
                'ZepEntityReader', 'filter_defined_entities',
                target=state.graph_id,
                defined_entity_types=defined_entity_types,
                enrich_with_edges=True,
            ) as step:
                filtered = reader.filter_defined_entities(
                    graph_id=state.graph_id,
                    defined_entity_types=defined_entity_types,
                    enrich_with_edges=True
                )
                step.metric(
                    entities=filtered.filtered_count,
                    entity_types=len(filtered.entity_types),
                )
                step.output(
                    entity_types=list(filtered.entity_types),
                    entity_names=[e.name for e in filtered.entities],
                )

            state.entities_count = filtered.filtered_count
            state.entity_types = list(filtered.entity_types)
            pipeline_log.end_stage(
                stage_handle,
                entities=filtered.filtered_count,
                entity_types=len(filtered.entity_types),
            )
            stage_handle = None

            if progress_callback:
                progress_callback(
                    "reading", 100,
                    t('progress.readingComplete', count=filtered.filtered_count),
                    current=filtered.filtered_count,
                    total=filtered.filtered_count
                )
            
            if filtered.filtered_count == 0:
                state.status = SimulationStatus.FAILED
                state.error = "No matching entities found; check that the graph was built correctly"
                self._save_simulation_state(state)
                raise ValueError(state.error)
            
            # ========== Stage 2: generate agent profiles ==========
            total_entities = len(filtered.entities)
            stage_handle = pipeline_log.begin_stage(
                'generating_profiles',
                entities=total_entities,
                use_llm=use_llm_for_profiles,
                parallel_count=parallel_profile_count,
            )

            if progress_callback:
                progress_callback(
                    "generating_profiles", 0,
                    t('progress.startGenerating'),
                    current=0,
                    total=total_entities
                )
            
            # Audience priors harvested during the graph build, apportioned
            # across the agent pool so the population matches how this audience
            # already argues about the category. Absent on projects built
            # before the corpus ran, which just means no priors.
            project = ProjectManager.get_project(state.project_id)
            corpus_distribution = project.corpus_distribution if project else None

            # Pass graph_id to enable Zep retrieval and get richer context
            generator = OasisProfileGenerator(
                graph_id=state.graph_id,
                corpus_distribution=corpus_distribution,
            )
            
            def profile_progress(current, total, msg):
                if progress_callback:
                    progress_callback(
                        "generating_profiles", 
                        int(current / total * 100), 
                        msg,
                        current=current,
                        total=total,
                        item_name=msg
                    )
            
            # Plan the cast before any persona is generated: the entity count
            # is whatever the source material yielded, the cast is bounded and
            # evenly spread. One LLM call supplies the age bands and the
            # markets the brief names; everything else is sampled.
            with pipeline_log.step(
                'AgentPopulation', 'plan_population',
                target=state.graph_id,
                entities=total_entities,
                max_agents=max_agents,
            ) as step:
                # The ontology already said what each type stands for, and it
                # said so while holding the type's own description. Its answer
                # wins; the hints call only fills in types it left blank, which
                # for an ontology built before the field existed is all of them.
                ontology_kinds = {
                    entity["name"]: entity["kind"]
                    for entity in ((project.ontology or {}).get("entity_types") or [])
                    if isinstance(entity, dict) and entity.get("kind") and entity.get("name")
                } if project else {}

                age_ranges, markets, hinted_kinds = derive_population_hints(
                    entity_types=[e.get_entity_type() or "Entity" for e in filtered.entities],
                    brief=simulation_requirement,
                )
                kinds = {**hinted_kinds, **ontology_kinds}
                slots = plan_population(
                    filtered.entities,
                    max_agents=max_agents,
                    age_ranges=age_ranges,
                    markets=markets,
                    kinds=kinds,
                )
                # The same classification decides the persona prompt, so a
                # segment is not written up as if it were an institution.
                generator.entity_kinds = kinds
                step.output(age_ranges=age_ranges, markets=markets, kinds=kinds)
                step.metric(
                    agents=len(slots),
                    clones=sum(1 for s in slots if s.is_clone),
                    markets=len(markets),
                )
            logger.info(
                f"population planned: {total_entities} entities -> {len(slots)} agents "
                f"(cap {max_agents}, {sum(1 for s in slots if s.is_clone)} clones)"
            )

            # Path used for incremental saves (Reddit JSON format preferred)
            realtime_output_path = None
            realtime_platform = "reddit"
            if state.enable_reddit:
                realtime_output_path = os.path.join(sim_dir, "reddit_profiles.json")
                realtime_platform = "reddit"
            elif state.enable_twitter:
                realtime_output_path = os.path.join(sim_dir, "twitter_profiles.csv")
                realtime_platform = "twitter"
            
            profiles = generator.generate_profiles_from_slots(
                slots=slots,
                use_llm=use_llm_for_profiles,
                progress_callback=profile_progress,
                graph_id=state.graph_id,  # graph_id enables Zep retrieval
                parallel_count=parallel_profile_count,  # Number generated in parallel
                realtime_output_path=realtime_output_path,  # Incremental save path
                output_platform=realtime_platform  # Output format
            )
            
            state.profiles_count = len(profiles)
            state.profiles_generated = len(profiles) > 0
            self._save_simulation_state(state)
            
            # Write the profile files (Twitter needs CSV, Reddit needs JSON).
            # Reddit was already saved incrementally during generation; writing
            # it again here guarantees a complete file.
            if progress_callback:
                progress_callback(
                    "generating_profiles", 95,
                    t('progress.savingProfiles'),
                    current=total_entities,
                    total=total_entities
                )
            
            if state.enable_reddit:
                with pipeline_log.step(
                    'OasisProfileGenerator', 'save_profiles', target='reddit',
                    profiles=len(profiles),
                ):
                    generator.save_profiles(
                        profiles=profiles,
                        file_path=os.path.join(sim_dir, "reddit_profiles.json"),
                        platform="reddit"
                    )

            if state.enable_twitter:
                # Twitter must be CSV - OASIS requires it
                with pipeline_log.step(
                    'OasisProfileGenerator', 'save_profiles', target='twitter',
                    profiles=len(profiles),
                ):
                    generator.save_profiles(
                        profiles=profiles,
                        file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                        platform="twitter"
                    )

            if progress_callback:
                progress_callback(
                    "generating_profiles", 100,
                    t('progress.profilesComplete', count=len(profiles)),
                    current=len(profiles),
                    total=len(profiles)
                )
            pipeline_log.end_stage(stage_handle, profiles=len(profiles))
            stage_handle = None

            # ========== Stage 3: generate the simulation config with the LLM ==========
            stage_handle = pipeline_log.begin_stage(
                'generating_config', entities=len(filtered.entities),
            )
            if progress_callback:
                progress_callback(
                    "generating_config", 0,
                    t('progress.analyzingRequirements'),
                    current=0,
                    total=3
                )
            
            config_generator = SimulationConfigGenerator()
            
            if progress_callback:
                progress_callback(
                    "generating_config", 30,
                    t('progress.callingLLMConfig'),
                    current=1,
                    total=3
                )
            
            sim_params = config_generator.generate_config(
                simulation_id=simulation_id,
                project_id=state.project_id,
                graph_id=state.graph_id,
                simulation_requirement=simulation_requirement,
                document_text=document_text,
                entities=filtered.entities,
                enable_twitter=state.enable_twitter,
                enable_reddit=state.enable_reddit,
                corpus_distribution=corpus_distribution,
                slots=slots
            )
            
            if progress_callback:
                progress_callback(
                    "generating_config", 70,
                    t('progress.savingConfigFiles'),
                    current=2,
                    total=3
                )
            
            # Write the config file
            config_path = os.path.join(sim_dir, "simulation_config.json")
            with pipeline_log.step(
                'SimulationManager', 'write_config', target=config_path,
            ) as step:
                config_json = sim_params.to_json()
                with open(config_path, 'w', encoding='utf-8') as f:
                    f.write(config_json)
                step.output_text(config_json)
                step.metric(config_bytes=len(config_json))

            state.config_generated = True
            state.config_reasoning = sim_params.generation_reasoning

            if progress_callback:
                progress_callback(
                    "generating_config", 100,
                    t('progress.configComplete'),
                    current=3,
                    total=3
                )
            pipeline_log.end_stage(stage_handle)
            stage_handle = None

            # The runner scripts stay in backend/scripts/ and are no longer copied
            # into the simulation directory; simulation_runner launches them from there.
            
            # Update the state
            state.status = SimulationStatus.READY
            self._save_simulation_state(state)
            
            logger.info(f"simulation prepared: {simulation_id}, "
                       f"entities={state.entities_count}, profiles={state.profiles_count}")
            
            return state
            
        except Exception as e:
            logger.error(f"simulation preparation failed: {simulation_id}, error={str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            if stage_handle is not None:
                pipeline_log.end_stage(
                    stage_handle,
                    status='error',
                    error=f"{type(e).__name__}: {e}",
                )
                stage_handle = None
            pipeline_log.action(
                'SimulationManager', 'prepare_failed',
                status='error', target=simulation_id,
                error=f"{type(e).__name__}: {e}",
            )
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise
    
    def get_simulation(self, simulation_id: str) -> Optional[SimulationState]:
        """Return the simulation state."""
        return self._load_simulation_state(simulation_id)
    
    def list_simulations(self, project_id: Optional[str] = None) -> List[SimulationState]:
        """List every simulation."""
        simulations = []
        
        if os.path.exists(self.SIMULATION_DATA_DIR):
            for sim_id in os.listdir(self.SIMULATION_DATA_DIR):
                # Skip hidden files (e.g. .DS_Store) and anything that is not a directory
                sim_path = os.path.join(self.SIMULATION_DATA_DIR, sim_id)
                if sim_id.startswith('.') or not os.path.isdir(sim_path):
                    continue
                
                state = self._load_simulation_state(sim_id)
                if state:
                    if project_id is None or state.project_id == project_id:
                        simulations.append(state)
        
        return simulations
    
    def delete_simulation(self, simulation_id: str) -> bool:
        """Delete a simulation directory and its cached state."""
        # Not _get_simulation_dir: that one creates the directory it returns.
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        with self._cache_lock:
            self._simulations.pop(simulation_id, None)

        if not os.path.isdir(sim_dir):
            return False

        shutil.rmtree(sim_dir)
        logger.info(f"deleted simulation: {simulation_id}")
        return True

    def get_profiles(self, simulation_id: str, platform: str = None) -> List[Dict[str, Any]]:
        """Return the agent profiles for a simulation."""
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"simulation does not exist: {simulation_id}")

        if platform is None:
            platform = state.get_default_platform()

        if platform not in {"twitter", "reddit"}:
            raise ValueError(f"unsupported platform: {platform}")

        sim_dir = self._get_simulation_dir(simulation_id)
        profile_path = os.path.join(
            sim_dir,
            "twitter_profiles.csv" if platform == "twitter" else "reddit_profiles.json",
        )
        
        if not os.path.exists(profile_path):
            return []

        if platform == "twitter":
            import csv

            with open(profile_path, 'r', encoding='utf-8', newline='') as f:
                return list(csv.DictReader(f))

        with open(profile_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_simulation_config(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """Return the simulation config."""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return None
        
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_run_instructions(self, simulation_id: str) -> Dict[str, str]:
        """Return the run instructions."""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        return {
            "simulation_dir": sim_dir,
            "scripts_dir": scripts_dir,
            "config_file": config_path,
            "commands": {
                "twitter": f"python {scripts_dir}/run_parallel_simulation.py --config {config_path} --twitter-only",
                "reddit": f"python {scripts_dir}/run_parallel_simulation.py --config {config_path} --reddit-only",
                "parallel": f"python {scripts_dir}/run_parallel_simulation.py --config {config_path}",
            },
            "instructions": (
                f"1. Activate the conda environment: conda activate Spiegel\n"
                f"2. Run the simulation (the script lives in {scripts_dir}):\n"
                f"   - Twitter only: python {scripts_dir}/run_parallel_simulation.py --config {config_path} --twitter-only\n"
                f"   - Reddit only: python {scripts_dir}/run_parallel_simulation.py --config {config_path} --reddit-only\n"
                f"   - Both platforms in parallel: python {scripts_dir}/run_parallel_simulation.py --config {config_path}"
            )
        }
