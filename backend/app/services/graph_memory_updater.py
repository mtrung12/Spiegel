"""
Graph memory update service.
Streams agent activity from a running simulation into the knowledge graph.
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime, timezone
from queue import Queue, Empty

from graphiti_core.nodes import EpisodeType

from ..models.project import ProjectManager
from ..utils.logger import get_logger
from ..utils.locale import get_locale, set_locale
from ..utils.ontology import (
    SEGMENT_EXTRACTION_INSTRUCTIONS,
    build_graphiti_ontology,
)
from ..utils.pipeline_logger import pipeline_log
from ..utils.graphiti_client import get_graphiti, run_sync

logger = get_logger('spiegel.graph_memory_updater')

# Ingestion is in-process now, so this is a bound on how long a drain may block
# simulation shutdown, not on how long another service's queue may take.
GRAPH_INGESTION_WAIT_TIMEOUT_SECONDS = 600


class GraphIngestionIncomplete(RuntimeError):
    """
    The drain finished, but some activity batches were rejected for good.

    Distinct from a drain that merely ran out of time, because the two need
    opposite handling. A timeout is retryable: the worker may still be making
    progress, so the updater stays registered and stopping it again can
    succeed. Rejected batches are not - they are never replayed, so ``stop()``
    would raise this again on every retry, forever. Treating the two alike is
    what turns a failing extraction model into a simulation whose report can
    never be generated.
    """


@dataclass
class AgentActivity:
    """A single agent activity record."""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str
    
    def to_episode_text(self) -> str:
        """
        Render the activity as text for the graph extractor.

        Uses a natural-language description so the extractor can pull entities and
        relationships from it. No simulation-specific prefix is added, which
        would otherwise skew the graph update.
        """
        # Each action type gets its own description
        action_descriptions = {
            "CREATE_POST": self._describe_create_post,
            "LIKE_POST": self._describe_like_post,
            "DISLIKE_POST": self._describe_dislike_post,
            "REPOST": self._describe_repost,
            "QUOTE_POST": self._describe_quote_post,
            "FOLLOW": self._describe_follow,
            "CREATE_COMMENT": self._describe_create_comment,
            "LIKE_COMMENT": self._describe_like_comment,
            "DISLIKE_COMMENT": self._describe_dislike_comment,
            "SEARCH_POSTS": self._describe_search,
            "SEARCH_USER": self._describe_search_user,
            "MUTE": self._describe_mute,
        }
        
        describe_func = action_descriptions.get(self.action_type, self._describe_generic)
        description = describe_func()
        
        # Keep the event time in the source text as well as episode metadata so
        # temporal extraction does not collapse a multi-action batch.
        return (
            f"[{self.timestamp}] [{self.platform} round {self.round_num}] "
            f"{self.agent_name}: {description}"
        )
    
    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f'posted: "{content}"'
        return "posted"
    
    def _describe_like_post(self) -> str:
        """Like a post - includes the post text and its author."""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f'liked {post_author}\'s post: "{post_content}"'
        elif post_content:
            return f'liked a post: "{post_content}"'
        elif post_author:
            return f"liked a post by {post_author}"
        return "liked a post"
    
    def _describe_dislike_post(self) -> str:
        """Dislike a post - includes the post text and its author."""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f'disliked {post_author}\'s post: "{post_content}"'
        elif post_content:
            return f'disliked a post: "{post_content}"'
        elif post_author:
            return f"disliked a post by {post_author}"
        return "disliked a post"
    
    def _describe_repost(self) -> str:
        """Repost - includes the original post text and its author."""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        
        if original_content and original_author:
            return f'reposted {original_author}\'s post: "{original_content}"'
        elif original_content:
            return f'reposted a post: "{original_content}"'
        elif original_author:
            return f"reposted a post by {original_author}"
        return "reposted a post"
    
    def _describe_quote_post(self) -> str:
        """Quote a post - includes the original text, its author and the quote comment."""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")
        
        base = ""
        if original_content and original_author:
            base = f'quoted {original_author}\'s post "{original_content}"'
        elif original_content:
            base = f'quoted a post "{original_content}"'
        elif original_author:
            base = f"quoted a post by {original_author}"
        else:
            base = "quoted a post"
        
        if quote_content:
            base += f', commenting: "{quote_content}"'
        return base
    
    def _describe_follow(self) -> str:
        """Follow a user - includes the followed user's name."""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f'followed the user "{target_user_name}"'
        return "followed a user"
    
    def _describe_create_comment(self) -> str:
        """Comment - includes the comment text and the post it replies to."""
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if content:
            if post_content and post_author:
                return f'commented on {post_author}\'s post "{post_content}": "{content}"'
            elif post_content:
                return f'commented on the post "{post_content}": "{content}"'
            elif post_author:
                return f'commented on {post_author}\'s post: "{content}"'
            return f'commented: "{content}"'
        return "posted a comment"
    
    def _describe_like_comment(self) -> str:
        """Like a comment - includes the comment text and its author."""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f'liked {comment_author}\'s comment: "{comment_content}"'
        elif comment_content:
            return f'liked a comment: "{comment_content}"'
        elif comment_author:
            return f"liked a comment by {comment_author}"
        return "liked a comment"
    
    def _describe_dislike_comment(self) -> str:
        """Dislike a comment - includes the comment text and its author."""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f'disliked {comment_author}\'s comment: "{comment_content}"'
        elif comment_content:
            return f'disliked a comment: "{comment_content}"'
        elif comment_author:
            return f"disliked a comment by {comment_author}"
        return "disliked a comment"
    
    def _describe_search(self) -> str:
        """Search posts - includes the search query."""
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f'searched for "{query}"' if query else "ran a search"
    
    def _describe_search_user(self) -> str:
        """Search users - includes the search query."""
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f'searched for the user "{query}"' if query else "searched for users"
    
    def _describe_mute(self) -> str:
        """Mute a user - includes the muted user's name."""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f'muted the user "{target_user_name}"'
        return "muted a user"
    
    def _describe_generic(self) -> str:
        # Fall back to a generic description for unknown action types
        return f"performed a {self.action_type} action"


class _DrainDeadlineExceeded(TimeoutError):
    def __init__(self, processed_count: int):
        super().__init__("graph updater drain deadline elapsed")
        self.processed_count = processed_count


class GraphMemoryUpdater:
    """
    Knowledge graph memory updater.

    Watches the simulation's action log and streams new agent activity into the
    graph. Activity is grouped per platform and flushed in batches of
    BATCH_SIZE.

    Each batch goes in through ``add_episode`` one at a time rather than the
    bulk path the document build uses. That is deliberate: sequential ingestion
    is what runs Graphiti's edge-invalidation pass, so a later round that
    contradicts an earlier one expires the old fact instead of leaving two
    contradictory edges in the graph. Watching opinion shift over rounds is the
    entire point of the simulation, and the report's active-versus-historical
    split reads exactly those timestamps.

    Every meaningful action is pushed to the graph; action_args carries the full
    context:
    - The text of a liked or disliked post
    - The text of a reposted or quoted post
    - The name of a followed or muted user
    - The text of a liked or disliked comment
    """
    
    # Batch size (how many records accumulate per platform before a flush)
    BATCH_SIZE = 5
    
    # Platform display names (console output)
    PLATFORM_DISPLAY_NAMES = {
        'twitter': 'World 1',
        'reddit': 'World 2',
    }
    
    # Delay between sends, in seconds, to avoid hammering the API
    SEND_INTERVAL = 0.5
    
    # Extraction quality falls off well before the model's context limit, and
    # an episode is one LLM call regardless of length. Leave room for future
    # source formatting changes.
    MAX_EPISODE_CHARS = 9_500

    def __init__(
        self,
        graph_id: str,
        simulation_id: Optional[str] = None,
    ):
        """
        Initialise the updater.

        Args:
            graph_id: Graph ID (the Graphiti group_id being written to)
            simulation_id: Simulation this updater belongs to
        """
        self.graph_id = graph_id
        self.simulation_id = simulation_id or "unknown"
        self.client = get_graphiti()
        # The ontology the graph was built with. Loaded once here rather than
        # per episode: without it, extraction from simulation activity falls
        # back to untyped Entity nodes and the report's entity-type filters
        # silently return nothing.
        self._entity_types, self._edge_types, self._edge_type_map = (
            self._load_ontology(graph_id)
        )

        # Activity queue
        self._activity_queue: Queue = Queue()
        
        # Per-platform activity buffers; each flushes once it reaches BATCH_SIZE
        self._platform_buffers: Dict[str, List[AgentActivity]] = {
            'twitter': [],
            'reddit': [],
        }
        self._buffer_lock = threading.Lock()
        self._acceptance_lock = threading.Lock()
        
        # Control flags
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        
        # Statistics
        self._total_activities = 0  # Activities actually queued
        self._total_sent = 0        # Batches successfully ingested
        self._total_items_sent = 0  # Activities successfully ingested
        self._failed_count = 0      # Batches that failed to send
        self._skipped_count = 0     # Activities filtered out (DO_NOTHING)
        self._failed_batches: List[Dict[str, Any]] = []

        logger.info(f"GraphMemoryUpdater initialised: graph_id={graph_id}, batch_size={self.BATCH_SIZE}")

    @staticmethod
    def _load_ontology(graph_id: str):
        """Recompile the stored project ontology for this graph.

        Zep held the ontology server-side once installed; Graphiti takes it per
        ingestion call, so it has to be recovered from the project that built
        the graph. A simulation whose project has since been deleted still
        ingests, just without typed extraction.
        """

        projects = ProjectManager.find_projects_by_graph_id(graph_id)
        ontology = next(
            (project.ontology for project in projects if project.ontology),
            None,
        )
        if ontology is None:
            logger.warning(
                "no stored ontology for graph %s; simulation activity will be "
                "extracted without entity types",
                graph_id,
            )
        return build_graphiti_ontology(ontology)

    def _get_platform_display_name(self, platform: str) -> str:
        """Return the display name for a platform."""
        return self.PLATFORM_DISPLAY_NAMES.get(platform.lower(), platform)
    
    def start(self):
        """Start the background worker thread."""
        if self._running:
            return

        # Capture locale before spawning background thread
        current_locale = get_locale()

        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            args=(current_locale,),
            daemon=True,
            name=f"GraphMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"GraphMemoryUpdater started: graph_id={self.graph_id}")
    
    def stop(self):
        """Drain the worker and flush tail events.

        There is no ingestion wait after the flush any more. ``add_episode``
        only returns once the episode's nodes and edges are committed, so a
        completed drain *is* a completed ingestion - the old poll over each
        episode's ``processed`` flag had nothing left to wait for.
        """
        deadline = time.time() + GRAPH_INGESTION_WAIT_TIMEOUT_SECONDS
        # Serialize the accepting->closed transition with add_activity's
        # check+enqueue operation. This closes the small race where a producer
        # could enqueue after both the worker and final flush had exited.
        with self._acceptance_lock:
            self._running = False

        if self._worker_thread and self._worker_thread.is_alive():
            join_timeout = max(0.0, deadline - time.time())
            self._worker_thread.join(timeout=join_timeout)
            if self._worker_thread.is_alive():
                raise TimeoutError(
                    f"graph updater worker did not stop within {join_timeout:.0f}s"
                )

        # The worker has drained the queue. Only now is it safe to flush
        # buffers; doing this before join loses an item already dequeued by the
        # worker but not yet buffered.
        self._flush_remaining(deadline=deadline)

        if self._failed_batches:
            raise GraphIngestionIncomplete(
                f"{len(self._failed_batches)} activity batch(es) failed; "
                "simulation graph ingestion is incomplete"
            )

        logger.info(f"GraphMemoryUpdater stopped: graph_id={self.graph_id}, "
                   f"total_activities={self._total_activities}, "
                   f"batches_sent={self._total_sent}, "
                   f"items_sent={self._total_items_sent}, "
                   f"failed={self._failed_count}, "
                   f"skipped={self._skipped_count}")
    
    def add_activity(self, activity: AgentActivity):
        """
        Queue an agent activity.

        Every meaningful action is queued:
        - CREATE_POST
        - CREATE_COMMENT
        - QUOTE_POST
        - SEARCH_POSTS
        - SEARCH_USER
        - LIKE_POST / DISLIKE_POST
        - REPOST
        - FOLLOW
        - MUTE
        - LIKE_COMMENT / DISLIKE_COMMENT

        action_args carries the full context (post text, user names, ...).

        Args:
            activity: The agent activity record
        """
        # Skip DO_NOTHING activities
        if activity.action_type == "DO_NOTHING":
            self._skipped_count += 1
            return

        with self._acceptance_lock:
            if not self._running:
                raise RuntimeError("graph updater is not running")
            self._activity_queue.put(activity)
            self._total_activities += 1
        logger.debug(f"queued activity for the graph: {activity.agent_name} - {activity.action_type}")
    
    def add_activity_from_dict(self, data: Dict[str, Any], platform: str):
        """
        Queue an activity from a parsed dict.

        Args:
            data: A dict parsed out of actions.jsonl
            platform: Platform name (twitter/reddit)
        """
        # Skip event-type records
        if "event_type" in data:
            return
        if data.get("success") is False:
            self._skipped_count += 1
            return
        
        activity = AgentActivity(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", 0),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )
        
        self.add_activity(activity)
    
    def _worker_loop(self, locale: str = 'zh'):
        """Background worker loop - flushes activity to the graph per platform."""
        # Own thread, own run scope, so these writes file under the simulation
        # they belong to rather than landing without a run id.
        with pipeline_log.run(
            run_id=self.simulation_id,
            kind='simulation_run',
            graph_id=self.graph_id,
        ), pipeline_log.stage('graph_memory_update'):
            self._worker_loop_body(locale)

    def _worker_loop_body(self, locale: str = 'zh'):
        """Drain the activity queue until the updater is stopped."""
        set_locale(locale)
        while self._running or not self._activity_queue.empty():
            try:
                # Try to pull an activity off the queue (1s timeout)
                try:
                    activity = self._activity_queue.get(timeout=1)
                    
                    # Append the activity to its platform buffer
                    platform = activity.platform.lower()
                    batch = None
                    with self._buffer_lock:
                        if platform not in self._platform_buffers:
                            self._platform_buffers[platform] = []
                        self._platform_buffers[platform].append(activity)
                        
                        # Flush once this platform reaches the batch size
                        if len(self._platform_buffers[platform]) >= self.BATCH_SIZE:
                            batch = self._platform_buffers[platform][:self.BATCH_SIZE]
                            self._platform_buffers[platform] = self._platform_buffers[platform][self.BATCH_SIZE:]

                    # Never hold the buffer lock across network I/O or sleep.
                    if batch:
                        self._send_batch_activities(batch, platform)
                        time.sleep(self.SEND_INTERVAL)
                    
                except Empty:
                    pass

            except Exception as e:
                logger.error(f"worker loop error: {e}")
                time.sleep(1)
    
    def _build_episode_payloads(
        self,
        activities: List[AgentActivity],
    ) -> List[tuple[List[AgentActivity], str]]:
        payloads: List[tuple[List[AgentActivity], str]] = []
        current_activities: List[AgentActivity] = []
        current_lines: List[str] = []
        current_length = 0

        for activity in activities:
            text = activity.to_episode_text()
            if len(text) > self.MAX_EPISODE_CHARS:
                marker = "... [truncated by Spiegel]"
                text = text[: self.MAX_EPISODE_CHARS - len(marker)] + marker
            projected_length = current_length + (1 if current_lines else 0) + len(text)
            if current_lines and projected_length > self.MAX_EPISODE_CHARS:
                payloads.append((current_activities, "\n".join(current_lines)))
                current_activities = []
                current_lines = []
                current_length = 0
            current_activities.append(activity)
            current_lines.append(text)
            current_length += (1 if len(current_lines) > 1 else 0) + len(text)

        if current_lines:
            payloads.append((current_activities, "\n".join(current_lines)))
        return payloads

    def _send_batch_activities(
        self,
        activities: List[AgentActivity],
        platform: str,
        *,
        deadline: float | None = None,
    ) -> int:
        """
        Flush a batch of activities into the graph as one combined text.

        Args:
            activities: The agent activities
            platform: Platform name
        """
        if not activities:
            return 0

        processed_count = 0
        for payload_activities, combined_text in self._build_episode_payloads(activities):
            if deadline is not None and time.time() >= deadline:
                raise _DrainDeadlineExceeded(processed_count)
            try:
                result = run_sync(
                    self.client.add_episode(
                        name=(
                            f"{self.simulation_id}:{platform}:"
                            f"r{payload_activities[-1].round_num}"
                        ),
                        episode_body=combined_text,
                        source=EpisodeType.text,
                        source_description="Spiegel simulation activity batch",
                        reference_time=self._to_datetime(
                            payload_activities[-1].timestamp
                        ),
                        group_id=self.graph_id,
                        entity_types=self._entity_types,
                        edge_types=self._edge_types,
                        edge_type_map=self._edge_type_map,
                        custom_extraction_instructions=SEGMENT_EXTRACTION_INSTRUCTIONS,
                    )
                )

                episode_uuid = result.episode.uuid
                self._total_sent += 1
                self._total_items_sent += len(payload_activities)
                display_name = self._get_platform_display_name(platform)
                logger.info(f"sent {len(payload_activities)} {display_name} activities to graph {self.graph_id}")
                logger.debug(f"batch content preview: {combined_text[:200]}...")

                # The episode body is agent prose, so it goes to the debug
                # stream and the action line carries only the batch shape.
                debug_id = pipeline_log.debug(
                    'GraphMemoryUpdater', 'graph_episode_added',
                    target=self.graph_id,
                    inputs={
                        'platform': platform,
                        'simulation_id': self.simulation_id,
                        'episode_uuid': str(episode_uuid),
                    },
                    output_text=combined_text,
                )
                pipeline_log.action(
                    'GraphMemoryUpdater', 'graph_episode_added',
                    target=self.graph_id,
                    metrics={
                        'platform': platform,
                        'activities': len(payload_activities),
                        'episode_chars': len(combined_text),
                        'first_round': min(a.round_num for a in payload_activities),
                        'last_round': max(a.round_num for a in payload_activities),
                    },
                    debug_id=debug_id,
                )

            except Exception as e:
                # add_episode is not idempotent: a retry after a partial write
                # duplicates the extracted facts rather than replacing them. Fail
                # closed and surface the incomplete batch to SimulationRunner.
                logger.error(f"batch ingestion failed; the write was not replayed: {e}")
                self._failed_count += 1
                self._failed_batches.append({
                    "platform": platform,
                    "activities": payload_activities,
                    "error": str(e),
                })
            finally:
                # Successes have a confirmed episode UUID; failures are kept
                # durably in _failed_batches and must never be replayed. Either
                # way this payload is accounted for before moving on.
                processed_count += len(payload_activities)
        return processed_count

    @staticmethod
    def _to_datetime(value: str) -> datetime:
        """Parse an activity timestamp into the aware datetime Graphiti wants.

        This is the episode's reference time, which is what edge validity
        intervals are anchored to - a naive value here would be compared against
        aware ones during invalidation and raise.
        """
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            return datetime.now(timezone.utc)
        return parsed.astimezone() if parsed.tzinfo is None else parsed

    def _flush_remaining(self, *, deadline: float | None = None):
        """Flush whatever is left in the queue and the buffers."""
        # Drain the queue into the buffers first
        while not self._activity_queue.empty():
            try:
                activity = self._activity_queue.get_nowait()
                platform = activity.platform.lower()
                with self._buffer_lock:
                    if platform not in self._platform_buffers:
                        self._platform_buffers[platform] = []
                    self._platform_buffers[platform].append(activity)
            except Empty:
                break
        
        for platform in list(self._platform_buffers):
            with self._buffer_lock:
                buffer = list(self._platform_buffers.get(platform, []))
            if not buffer:
                continue
            display_name = self._get_platform_display_name(platform)
            logger.info(f"flushing the remaining {len(buffer)} {display_name} activities")
            if deadline is not None and time.time() >= deadline:
                raise TimeoutError(
                    "graph updater drain deadline elapsed before flushing all activities"
                )
            try:
                processed_count = self._send_batch_activities(
                    buffer,
                    platform,
                    deadline=deadline,
                )
            except _DrainDeadlineExceeded as error:
                with self._buffer_lock:
                    del self._platform_buffers[platform][:error.processed_count]
                raise TimeoutError(str(error)) from error
            else:
                with self._buffer_lock:
                    del self._platform_buffers[platform][:processed_count]

    def get_stats(self) -> Dict[str, Any]:
        """Return the statistics."""
        with self._buffer_lock:
            buffer_sizes = {p: len(b) for p, b in self._platform_buffers.items()}

        return {
            "graph_id": self.graph_id,
            "batch_size": self.BATCH_SIZE,
            "total_activities": self._total_activities,  # Total activities queued
            "batches_sent": self._total_sent,            # Batches sent successfully
            "items_sent": self._total_items_sent,        # Activities sent successfully
            "failed_count": self._failed_count,          # Batches that failed to send
            "skipped_count": self._skipped_count,        # Activities filtered out (DO_NOTHING)
            "queue_size": self._activity_queue.qsize(),
            "buffer_sizes": buffer_sizes,                # Buffer size per platform
            "running": self._running,
        }


class GraphMemoryManager:
    """
    Registry of graph memory updaters across simulations.

    Each simulation gets its own updater instance.
    """
    
    _updaters: Dict[str, GraphMemoryUpdater] = {}
    _lock = threading.Lock()
    
    @classmethod
    def create_updater(cls, simulation_id: str, graph_id: str) -> GraphMemoryUpdater:
        """
        Create the graph memory updater for a simulation.

        Args:
            simulation_id: Simulation ID
            graph_id: Graph ID

        Returns:
            The GraphMemoryUpdater instance
        """
        with cls._lock:
            # Stop the existing updater before replacing it
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
            
            updater = GraphMemoryUpdater(
                graph_id,
                simulation_id=simulation_id,
            )
            updater.start()
            cls._updaters[simulation_id] = updater
            cls._stop_all_done = False
            
            logger.info(f"created graph memory updater: simulation_id={simulation_id}, graph_id={graph_id}")
            return updater
    
    @classmethod
    def get_updater(cls, simulation_id: str) -> Optional[GraphMemoryUpdater]:
        """Return the updater for a simulation."""
        with cls._lock:
            return cls._updaters.get(simulation_id)

    @classmethod
    def get_simulation_ids_for_graph(cls, graph_id: str) -> List[str]:
        """Return simulations whose updater still owns or drains this graph."""

        with cls._lock:
            return sorted(
                simulation_id
                for simulation_id, updater in cls._updaters.items()
                if updater.graph_id == graph_id
            )

    @classmethod
    def get_simulation_ids(cls) -> List[str]:
        """Return every simulation with a retained updater."""

        with cls._lock:
            return sorted(cls._updaters)

    @classmethod
    def discard_inactive_updater(cls, simulation_id: str) -> bool:
        """Discard a failed, fully stopped updater during graph destruction."""

        with cls._lock:
            updater = cls._updaters.get(simulation_id)
            if updater is None:
                return False
            worker_alive = bool(
                updater._worker_thread and updater._worker_thread.is_alive()
            )
            if updater._running or worker_alive:
                raise RuntimeError(
                    f"graph updater for {simulation_id} is still active"
                )
            cls._updaters.pop(simulation_id, None)
        logger.warning(
            "Discarded incomplete graph updater during explicit graph deletion: "
            "simulation_id=%s, graph_id=%s",
            simulation_id,
            updater.graph_id,
        )
        return True
    
    @classmethod
    def stop_updater(cls, simulation_id: str):
        """Stop and remove the updater for a simulation."""
        with cls._lock:
            updater = cls._updaters.get(simulation_id)
        if updater is None:
            return

        # Do not hold the manager lock through up to several minutes of Cloud
        # polling. Crucially, only remove the updater after a drain that either
        # succeeded or can never succeed; a retryable failure keeps it visible
        # to the report/deletion barriers so it can be stopped again.
        try:
            updater.stop()
        except GraphIngestionIncomplete:
            # Rejected batches are never replayed, so every retry raises this
            # again. Keeping the registration would leave the barriers reading
            # "ingestion still pending" for the life of the process. Drop it
            # and let the caller record the loss instead.
            with cls._lock:
                if cls._updaters.get(simulation_id) is updater:
                    cls._updaters.pop(simulation_id, None)
            raise
        with cls._lock:
            if cls._updaters.get(simulation_id) is updater:
                cls._updaters.pop(simulation_id, None)
        logger.info(f"stopped graph memory updater: simulation_id={simulation_id}")
    
    # Guards against stop_all being called twice
    _stop_all_done = False
    
    @classmethod
    def stop_all(cls):
        """Stop every updater."""
        # Guard against a repeat call
        if cls._stop_all_done:
            return

        with cls._lock:
            simulation_ids = list(cls._updaters)

        errors = []
        for simulation_id in simulation_ids:
            try:
                cls.stop_updater(simulation_id)
            except Exception as error:
                # Keep a failed updater registered so the caller can retry and
                # lifecycle/report guards still see the incomplete ingestion.
                logger.error(
                    "failed to stop updater: simulation_id=%s, error=%s",
                    simulation_id,
                    error,
                )
                errors.append((simulation_id, error))

        with cls._lock:
            cls._stop_all_done = not cls._updaters

        if errors:
            details = "; ".join(
                f"{simulation_id}: {error}"
                for simulation_id, error in errors
            )
            raise RuntimeError(f"some graph updaters did not stop cleanly: {details}")
        logger.info("stopped all graph memory updaters")
    
