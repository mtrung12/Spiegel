"""
Preset script for the dual-platform OASIS simulation.
Runs the Twitter and Reddit simulations side by side off one config file.

Features:
- Dual-platform (Twitter + Reddit) parallel simulation
- Keeps the environment alive after the run and waits for commands
- Accepts interview commands over IPC
- Supports single-agent and batch interviews
- Supports a remote shutdown command

Usage:
    python run_parallel_simulation.py --config simulation_config.json
    python run_parallel_simulation.py --config simulation_config.json --no-wait  # close as soon as it finishes
    python run_parallel_simulation.py --config simulation_config.json --twitter-only
    python run_parallel_simulation.py --config simulation_config.json --reddit-only

Log layout:
    sim_xxx/
    ├── twitter/
    │   └── actions.jsonl    # Twitter action log
    ├── reddit/
    │   └── actions.jsonl    # Reddit action log
    ├── simulation.log       # main simulation process log
    └── run_state.json       # run state, queried through the API
"""

# ============================================================
# Fix the Windows encoding problem: force UTF-8 before any other import.
# The OASIS third-party library opens files without an explicit encoding.
# ============================================================
import sys
import os

if sys.platform == 'win32':
    # Make UTF-8 Python's default I/O encoding
    # This covers every open() call that omits an encoding
    os.environ.setdefault('PYTHONUTF8', '1')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    
    # Reconfigure the standard streams to UTF-8, which fixes console mojibake
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    
    # Force the default encoding, which is what open() picks up.
    # It really has to be set at interpreter start; doing it at runtime may not stick,
    # so the built-in open is monkey-patched as well.
    import builtins
    _original_open = builtins.open
    
    def _utf8_open(file, mode='r', buffering=-1, encoding=None, errors=None, 
                   newline=None, closefd=True, opener=None):
        """
        Wrap open() so text mode defaults to UTF-8.
        This fixes third-party libraries such as OASIS that read files without
        specifying an encoding.
        """
        # Only for text mode with no encoding given; binary mode is untouched
        if encoding is None and 'b' not in mode:
            encoding = 'utf-8'
        return _original_open(file, mode, buffering, encoding, errors, 
                              newline, closefd, opener)
    
    builtins.open = _utf8_open

import argparse
import asyncio
import json
import logging
import multiprocessing
import random
import signal
import sqlite3
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple


# Global, used by the signal handlers
_shutdown_event = None
_cleanup_done = False

# Add the backend directory to sys.path
# This script always lives in backend/scripts/
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
_project_root = os.path.abspath(os.path.join(_backend_dir, '..'))
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _backend_dir)

# Load the .env file from the project root (LLM_API_KEY and friends)
from dotenv import load_dotenv
_env_file = os.path.join(_project_root, '.env')
if os.path.exists(_env_file):
    load_dotenv(_env_file)
    print(f"Loaded environment config: {_env_file}")
else:
    # Try backend/.env
    _backend_env = os.path.join(_backend_dir, '.env')
    if os.path.exists(_backend_env):
        load_dotenv(_backend_env)
        print(f"Loaded environment config: {_backend_env}")


class MaxTokensWarningFilter(logging.Filter):
    """Drop the camel-ai max_tokens warning. Leaving max_tokens unset is deliberate:
    the model decides for itself."""
    
    def filter(self, record):
        # Drop log records carrying the max_tokens warning
        if "max_tokens" in record.getMessage() and "Invalid or missing" in record.getMessage():
            return False
        return True


# Attach the filter at import time, so it is live before any camel code runs
logging.getLogger().addFilter(MaxTokensWarningFilter())


def disable_oasis_logging():
    """
    Silence the OASIS library's verbose logging.
    OASIS logs every agent observation and action, which is far too noisy; we
    use our own action_logger instead.
    """
    # Silence every OASIS logger
    oasis_loggers = [
        "social.agent",
        "social.twitter", 
        "social.rec",
        "oasis.env",
        "table",
    ]
    
    for logger_name in oasis_loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.CRITICAL)  # only record critical errors
        logger.handlers.clear()
        logger.propagate = False


def init_logging_for_simulation(simulation_dir: str):
    """
    Set up logging for the simulation.
    
    Args:
        simulation_dir: Path to the simulation directory
    """
    # Silence the verbose OASIS logging
    disable_oasis_logging()
    
    # Remove the previous log directory, if any
    old_log_dir = os.path.join(simulation_dir, "log")
    if os.path.exists(old_log_dir):
        import shutil
        shutil.rmtree(old_log_dir, ignore_errors=True)


from action_logger import SimulationLogManager, PlatformActionLogger
from agent_activity import normalize_active_hours
from token_meter import METER as TOKEN_METER, instrument_model

try:
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    import oasis
    from oasis import (
        ActionType,
        LLMAction,
        ManualAction,
        generate_twitter_agent_graph,
    )
    # Local builder: OASIS's own one cannot omit the country clause, and cannot
    # forward a custom system-message template. See scripts/oasis_graph.py.
    from oasis_graph import generate_reddit_agent_graph
except ImportError as e:
    print(f"Error: missing dependency {e}")
    print("Install it first: pip install oasis-ai camel-ai")
    sys.exit(1)


# Actions available on Twitter. INTERVIEW is excluded: it can only be
# triggered by hand, through a ManualAction.
TWITTER_ACTIONS = [
    ActionType.CREATE_POST,
    ActionType.LIKE_POST,
    ActionType.REPOST,
    ActionType.FOLLOW,
    ActionType.DO_NOTHING,
    ActionType.QUOTE_POST,
    # Replies on X. DISLIKE_COMMENT is left out: X has no downvote.
    ActionType.CREATE_COMMENT,
    ActionType.LIKE_COMMENT,
]

# Actions available on Reddit. INTERVIEW is excluded: it can only be
# triggered by hand, through a ManualAction.
REDDIT_ACTIONS = [
    ActionType.LIKE_POST,
    ActionType.DISLIKE_POST,
    ActionType.CREATE_POST,
    ActionType.CREATE_COMMENT,
    ActionType.LIKE_COMMENT,
    ActionType.DISLIKE_COMMENT,
    ActionType.SEARCH_POSTS,
    ActionType.SEARCH_USER,
    ActionType.TREND,
    ActionType.REFRESH,
    ActionType.DO_NOTHING,
    ActionType.FOLLOW,
    ActionType.MUTE,
]


# IPC constants
IPC_COMMANDS_DIR = "ipc_commands"
IPC_RESPONSES_DIR = "ipc_responses"
ENV_STATUS_FILE = "env_status.json"

# How long command-wait mode stays up with no IPC command before closing the
# environment itself. Without it a run whose client went away (tab closed,
# browser crashed) never exits, so SimulationRunner's monitor never publishes a
# terminal status and the report step waits on a process that will never end.
DEFAULT_IDLE_TIMEOUT = 3600.0

class CommandType:
    """Command type constants."""
    INTERVIEW = "interview"
    BATCH_INTERVIEW = "batch_interview"
    CLOSE_ENV = "close_env"


class ParallelIPCHandler:
    """
    Dual-platform IPC command handler.
    
    Owns both platform environments and services the interview commands.
    """
    
    def __init__(
        self,
        simulation_dir: str,
        twitter_env=None,
        twitter_agent_graph=None,
        reddit_env=None,
        reddit_agent_graph=None
    ):
        self.simulation_dir = simulation_dir
        self.twitter_env = twitter_env
        self.twitter_agent_graph = twitter_agent_graph
        self.reddit_env = reddit_env
        self.reddit_agent_graph = reddit_agent_graph
        
        self.commands_dir = os.path.join(simulation_dir, IPC_COMMANDS_DIR)
        self.responses_dir = os.path.join(simulation_dir, IPC_RESPONSES_DIR)
        self.status_file = os.path.join(simulation_dir, ENV_STATUS_FILE)

        # Monotonic timestamp of the last serviced command. The command-wait
        # loop uses it to close an environment nobody is interviewing, so a run
        # that finishes while no client is attached still reaches a terminal
        # status instead of parking here forever.
        self.last_command_at = time.monotonic()
        
        # Make sure the directories exist
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)
    
    def update_status(self, status: str):
        """Update the environment status."""
        with open(self.status_file, 'w', encoding='utf-8') as f:
            json.dump({
                "status": status,
                "twitter_available": self.twitter_env is not None,
                "reddit_available": self.reddit_env is not None,
                "timestamp": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def poll_command(self) -> Optional[Dict[str, Any]]:
        """Poll for a pending command."""
        if not os.path.exists(self.commands_dir):
            return None
        
        # Read the command files, oldest first
        command_files = []
        for filename in os.listdir(self.commands_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.commands_dir, filename)
                command_files.append((filepath, os.path.getmtime(filepath)))
        
        command_files.sort(key=lambda x: x[1])
        
        for filepath, _ in command_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
        
        return None
    
    def send_response(self, command_id: str, status: str, result: Dict = None, error: str = None):
        """Send a response."""
        # A long batch interview can outlast the idle timeout on its own, so the
        # deadline is measured from when a command *finishes*, not only from
        # when it arrived.
        self.last_command_at = time.monotonic()

        response = {
            "command_id": command_id,
            "status": status,
            "result": result,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }
        
        response_file = os.path.join(self.responses_dir, f"{command_id}.json")
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(response, f, ensure_ascii=False, indent=2)
        
        # Remove the command file
        command_file = os.path.join(self.commands_dir, f"{command_id}.json")
        try:
            os.remove(command_file)
        except OSError:
            pass
    
    def _get_env_and_graph(self, platform: str):
        """
        Return the environment and agent_graph for a platform.
        
        Args:
            platform: Platform name ("twitter" or "reddit")
            
        Returns:
            (env, agent_graph, platform_name), or (None, None, None)
        """
        if platform == "twitter" and self.twitter_env:
            return self.twitter_env, self.twitter_agent_graph, "twitter"
        elif platform == "reddit" and self.reddit_env:
            return self.reddit_env, self.reddit_agent_graph, "reddit"
        else:
            return None, None, None
    
    async def _interview_single_platform(self, agent_id: int, prompt: str, platform: str) -> Dict[str, Any]:
        """
        Run an interview on one platform.
        
        Returns:
            The result dict, or a dict carrying an error
        """
        env, agent_graph, actual_platform = self._get_env_and_graph(platform)
        
        if not env or not agent_graph:
            return {"platform": platform, "error": f"the {platform} platform is unavailable"}
        
        try:
            agent = agent_graph.get_agent(agent_id)
            interview_action = ManualAction(
                action_type=ActionType.INTERVIEW,
                action_args={"prompt": prompt}
            )
            actions = {agent: interview_action}
            await env.step(actions)
            
            result = self._get_interview_result(agent_id, actual_platform)
            result["platform"] = actual_platform
            return result
            
        except Exception as e:
            return {"platform": platform, "error": str(e)}
    
    async def handle_interview(self, command_id: str, agent_id: int, prompt: str, platform: str = None) -> bool:
        """
        Handle a single-agent interview command.
        
        Args:
            command_id: Command ID
            agent_id: Agent ID
            prompt: Interview question
            platform: Target platform (optional)
                - "twitter": Twitter only
                - "reddit": Reddit only
                - None: interview both platforms and return the combined result
            
        Returns:
            True on success, False on failure
        """
        # A platform was given: interview only that one
        if platform in ("twitter", "reddit"):
            result = await self._interview_single_platform(agent_id, prompt, platform)
            
            if "error" in result:
                self.send_response(command_id, "failed", error=result["error"])
                print(f"  Interview failed: agent_id={agent_id}, platform={platform}, error={result['error']}")
                return False
            else:
                self.send_response(command_id, "completed", result=result)
                print(f"  Interview complete: agent_id={agent_id}, platform={platform}")
                return True
        
        # No platform given: interview both
        if not self.twitter_env and not self.reddit_env:
            self.send_response(command_id, "failed", error="no simulation environment is available")
            return False
        
        results = {
            "agent_id": agent_id,
            "prompt": prompt,
            "platforms": {}
        }
        success_count = 0
        
        # Interview both platforms in parallel
        tasks = []
        platforms_to_interview = []
        
        if self.twitter_env:
            tasks.append(self._interview_single_platform(agent_id, prompt, "twitter"))
            platforms_to_interview.append("twitter")
        
        if self.reddit_env:
            tasks.append(self._interview_single_platform(agent_id, prompt, "reddit"))
            platforms_to_interview.append("reddit")
        
        # Run them concurrently
        platform_results = await asyncio.gather(*tasks)
        
        for platform_name, platform_result in zip(platforms_to_interview, platform_results):
            results["platforms"][platform_name] = platform_result
            if "error" not in platform_result:
                success_count += 1
        
        if success_count > 0:
            self.send_response(command_id, "completed", result=results)
            print(f"  Interview complete: agent_id={agent_id}, platforms succeeded={success_count}/{len(platforms_to_interview)}")
            return True
        else:
            errors = [f"{p}: {r.get('error', 'unknown error')}" for p, r in results["platforms"].items()]
            self.send_response(command_id, "failed", error="; ".join(errors))
            print(f"  Interview failed: agent_id={agent_id}, every platform failed")
            return False
    
    async def handle_batch_interview(self, command_id: str, interviews: List[Dict], platform: str = None) -> bool:
        """
        Handle a batch interview command.
        
        Args:
            command_id: Command ID
            interviews: [{"agent_id": int, "prompt": str, "platform": str(optional)}, ...]
            platform: Default platform (an item's own platform wins)
                - "twitter": Twitter only
                - "reddit": Reddit only
                - None: interview every agent on both platforms
        """
        # Group by platform
        twitter_interviews = []
        reddit_interviews = []
        both_platforms_interviews = []  # Items that hit both platforms
        
        for interview in interviews:
            item_platform = interview.get("platform", platform)
            if item_platform == "twitter":
                twitter_interviews.append(interview)
            elif item_platform == "reddit":
                reddit_interviews.append(interview)
            else:
                # No platform given: use both
                both_platforms_interviews.append(interview)
        
        # Fan both_platforms_interviews out across the two platforms
        if both_platforms_interviews:
            if self.twitter_env:
                twitter_interviews.extend(both_platforms_interviews)
            if self.reddit_env:
                reddit_interviews.extend(both_platforms_interviews)
        
        results = {}
        
        # Run the Twitter interviews
        if twitter_interviews and self.twitter_env:
            try:
                twitter_actions = {}
                for interview in twitter_interviews:
                    agent_id = interview.get("agent_id")
                    prompt = interview.get("prompt", "")
                    try:
                        agent = self.twitter_agent_graph.get_agent(agent_id)
                        twitter_actions[agent] = ManualAction(
                            action_type=ActionType.INTERVIEW,
                            action_args={"prompt": prompt}
                        )
                    except Exception as e:
                        print(f"  Warning: cannot fetch Twitter agent {agent_id}: {e}")
                
                if twitter_actions:
                    await self.twitter_env.step(twitter_actions)
                    
                    for interview in twitter_interviews:
                        agent_id = interview.get("agent_id")
                        result = self._get_interview_result(agent_id, "twitter")
                        result["platform"] = "twitter"
                        results[f"twitter_{agent_id}"] = result
            except Exception as e:
                print(f"  Twitter batch interview failed: {e}")
        
        # Run the Reddit interviews
        if reddit_interviews and self.reddit_env:
            try:
                reddit_actions = {}
                for interview in reddit_interviews:
                    agent_id = interview.get("agent_id")
                    prompt = interview.get("prompt", "")
                    try:
                        agent = self.reddit_agent_graph.get_agent(agent_id)
                        reddit_actions[agent] = ManualAction(
                            action_type=ActionType.INTERVIEW,
                            action_args={"prompt": prompt}
                        )
                    except Exception as e:
                        print(f"  Warning: cannot fetch Reddit agent {agent_id}: {e}")
                
                if reddit_actions:
                    await self.reddit_env.step(reddit_actions)
                    
                    for interview in reddit_interviews:
                        agent_id = interview.get("agent_id")
                        result = self._get_interview_result(agent_id, "reddit")
                        result["platform"] = "reddit"
                        results[f"reddit_{agent_id}"] = result
            except Exception as e:
                print(f"  Reddit batch interview failed: {e}")
        
        if results:
            self.send_response(command_id, "completed", result={
                "interviews_count": len(results),
                "results": results
            })
            print(f"  Batch interview complete: {len(results)} agents")
            return True
        else:
            self.send_response(command_id, "failed", error="no interview succeeded")
            return False
    
    def _get_interview_result(self, agent_id: int, platform: str) -> Dict[str, Any]:
        """Read the latest interview result out of the database."""
        db_path = os.path.join(self.simulation_dir, f"{platform}_simulation.db")
        
        result = {
            "agent_id": agent_id,
            "response": None,
            "timestamp": None
        }
        
        if not os.path.exists(db_path):
            return result
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Query the newest interview record
            cursor.execute("""
                SELECT user_id, info, created_at
                FROM trace
                WHERE action = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (ActionType.INTERVIEW.value, agent_id))
            
            row = cursor.fetchone()
            if row:
                user_id, info_json, created_at = row
                try:
                    info = json.loads(info_json) if info_json else {}
                    result["response"] = info.get("response", info)
                    result["timestamp"] = created_at
                except json.JSONDecodeError:
                    result["response"] = info_json
            
            conn.close()
            
        except Exception as e:
            print(f"  Failed to read interview results: {e}")
        
        return result
    
    async def process_commands(self) -> bool:
        """
        Handle every pending command.
        
        Returns:
            True to keep running, False to exit
        """
        command = self.poll_command()
        if not command:
            return True

        self.last_command_at = time.monotonic()

        command_id = command.get("command_id")
        command_type = command.get("command_type")
        args = command.get("args", {})
        
        print(f"\nReceived IPC command: {command_type}, id={command_id}")
        
        if command_type == CommandType.INTERVIEW:
            await self.handle_interview(
                command_id,
                args.get("agent_id", 0),
                args.get("prompt", ""),
                args.get("platform")
            )
            return True
            
        elif command_type == CommandType.BATCH_INTERVIEW:
            await self.handle_batch_interview(
                command_id,
                args.get("interviews", []),
                args.get("platform")
            )
            return True
            
        elif command_type == CommandType.CLOSE_ENV:
            print("Received close-environment command")
            self.send_response(command_id, "completed", result={"message": "environment is shutting down"})
            return False
        
        else:
            self.send_response(command_id, "failed", error=f"unknown command type: {command_type}")
            return True


def load_config(config_path: str) -> Dict[str, Any]:
    """Load the config file."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# Non-core action types to filter out; they carry little analytical value
FILTERED_ACTIONS = {'refresh', 'sign_up'}

# Action type mapping: the name in the database -> the canonical name
ACTION_TYPE_MAP = {
    'create_post': 'CREATE_POST',
    'like_post': 'LIKE_POST',
    'dislike_post': 'DISLIKE_POST',
    'repost': 'REPOST',
    'quote_post': 'QUOTE_POST',
    'follow': 'FOLLOW',
    'mute': 'MUTE',
    'create_comment': 'CREATE_COMMENT',
    'like_comment': 'LIKE_COMMENT',
    'dislike_comment': 'DISLIKE_COMMENT',
    'search_posts': 'SEARCH_POSTS',
    'search_user': 'SEARCH_USER',
    'trend': 'TREND',
    'do_nothing': 'DO_NOTHING',
    'interview': 'INTERVIEW',
}


def get_agent_names_from_config(config: Dict[str, Any]) -> Dict[int, str]:
    """
    Build the agent_id -> entity_name mapping from simulation_config.
    
    That lets actions.jsonl show the real entity name rather than a placeholder
    like "Agent_0".
    
    Args:
        config: The contents of simulation_config.json
        
    Returns:
        The agent_id -> entity_name mapping
    """
    agent_names = {}
    agent_configs = config.get("agent_configs", [])
    
    for agent_config in agent_configs:
        agent_id = agent_config.get("agent_id")
        entity_name = agent_config.get("entity_name", f"Agent_{agent_id}")
        if agent_id is not None:
            agent_names[agent_id] = entity_name
    
    return agent_names


def fetch_new_actions_from_db(
    db_path: str,
    last_rowid: int,
    agent_names: Dict[int, str]
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Read the new action records out of the database and enrich them with context.
    
    Args:
        db_path: Path to the database file
        last_rowid: Highest rowid read last time. rowid is used instead of
            created_at because the two platforms format created_at differently.
        agent_names: The agent_id -> agent_name mapping
        
    Returns:
        (actions_list, new_last_rowid)
        - actions_list: the actions, each carrying agent_id, agent_name,
          action_type and action_args (with the context filled in)
        - new_last_rowid: the new highest rowid
    """
    actions = []
    new_last_rowid = last_rowid
    
    if not os.path.exists(db_path):
        return actions, new_last_rowid
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Track progress by rowid, SQLite's built-in autoincrementing column.
        # That sidesteps the created_at format difference: Twitter stores an
        # integer, Reddit a datetime string.
        cursor.execute("""
            SELECT rowid, user_id, action, info
            FROM trace
            WHERE rowid > ?
            ORDER BY rowid ASC
        """, (last_rowid,))
        
        for rowid, user_id, action, info_json in cursor.fetchall():
            # Advance the highest rowid
            new_last_rowid = rowid
            
            # Drop the non-core actions
            if action in FILTERED_ACTIONS:
                continue
            
            # Parse the action arguments
            try:
                action_args = json.loads(info_json) if info_json else {}
            except json.JSONDecodeError:
                action_args = {}
            
            # Trim action_args to the key fields, keeping their content in full
            simplified_args = {}
            if 'content' in action_args:
                simplified_args['content'] = action_args['content']
            if 'post_id' in action_args:
                simplified_args['post_id'] = action_args['post_id']
            if 'comment_id' in action_args:
                simplified_args['comment_id'] = action_args['comment_id']
            if 'quoted_id' in action_args:
                simplified_args['quoted_id'] = action_args['quoted_id']
            if 'new_post_id' in action_args:
                simplified_args['new_post_id'] = action_args['new_post_id']
            if 'follow_id' in action_args:
                simplified_args['follow_id'] = action_args['follow_id']
            if 'query' in action_args:
                simplified_args['query'] = action_args['query']
            if 'like_id' in action_args:
                simplified_args['like_id'] = action_args['like_id']
            if 'dislike_id' in action_args:
                simplified_args['dislike_id'] = action_args['dislike_id']
            
            # Map the action type name
            action_type = ACTION_TYPE_MAP.get(action, action.upper())
            
            # Fill in the context: post content, user names, and so on
            _enrich_action_context(cursor, action_type, simplified_args, agent_names)
            
            actions.append({
                'agent_id': user_id,
                'agent_name': agent_names.get(user_id, f'Agent_{user_id}'),
                'action_type': action_type,
                'action_args': simplified_args,
            })
        
        conn.close()
    except Exception as e:
        print(f"Failed to read actions from the database: {e}")
    
    return actions, new_last_rowid


def _enrich_action_context(
    cursor,
    action_type: str,
    action_args: Dict[str, Any],
    agent_names: Dict[int, str]
) -> None:
    """
    Fill in an action's context: post content, user names, and so on.
    
    Args:
        cursor: Database cursor
        action_type: Action type
        action_args: Action arguments (mutated in place)
        agent_names: The agent_id -> agent_name mapping
    """
    try:
        # Like/dislike a post: fill in the post content and its author
        if action_type in ('LIKE_POST', 'DISLIKE_POST'):
            post_id = action_args.get('post_id')
            if post_id:
                post_info = _get_post_info(cursor, post_id, agent_names)
                if post_info:
                    action_args['post_content'] = post_info.get('content', '')
                    action_args['post_author_name'] = post_info.get('author_name', '')
        
        # Repost: fill in the original post content and its author
        elif action_type == 'REPOST':
            new_post_id = action_args.get('new_post_id')
            if new_post_id:
                # A repost's original_post_id points at the original
                cursor.execute("""
                    SELECT original_post_id FROM post WHERE post_id = ?
                """, (new_post_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    original_post_id = row[0]
                    original_info = _get_post_info(cursor, original_post_id, agent_names)
                    if original_info:
                        action_args['original_content'] = original_info.get('content', '')
                        action_args['original_author_name'] = original_info.get('author_name', '')
        
        # Quote a post: fill in the original content, its author and the quote
        elif action_type == 'QUOTE_POST':
            quoted_id = action_args.get('quoted_id')
            new_post_id = action_args.get('new_post_id')
            
            if quoted_id:
                original_info = _get_post_info(cursor, quoted_id, agent_names)
                if original_info:
                    action_args['original_content'] = original_info.get('content', '')
                    action_args['original_author_name'] = original_info.get('author_name', '')
            
            # Read the quote's own comment text (quote_content)
            if new_post_id:
                cursor.execute("""
                    SELECT quote_content FROM post WHERE post_id = ?
                """, (new_post_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    action_args['quote_content'] = row[0]
        
        # Follow: fill in the followed user's name
        elif action_type == 'FOLLOW':
            follow_id = action_args.get('follow_id')
            if follow_id:
                # Read followee_id from the follow table
                cursor.execute("""
                    SELECT followee_id FROM follow WHERE follow_id = ?
                """, (follow_id,))
                row = cursor.fetchone()
                if row:
                    followee_id = row[0]
                    target_name = _get_user_name(cursor, followee_id, agent_names)
                    if target_name:
                        action_args['target_user_name'] = target_name
        
        # Mute: fill in the muted user's name
        elif action_type == 'MUTE':
            # Read user_id or target_id out of action_args
            target_id = action_args.get('user_id') or action_args.get('target_id')
            if target_id:
                target_name = _get_user_name(cursor, target_id, agent_names)
                if target_name:
                    action_args['target_user_name'] = target_name
        
        # Like/dislike a comment: fill in the comment content and its author
        elif action_type in ('LIKE_COMMENT', 'DISLIKE_COMMENT'):
            comment_id = action_args.get('comment_id')
            if comment_id:
                comment_info = _get_comment_info(cursor, comment_id, agent_names)
                if comment_info:
                    action_args['comment_content'] = comment_info.get('content', '')
                    action_args['comment_author_name'] = comment_info.get('author_name', '')
        
        # Comment: fill in the post being replied to
        elif action_type == 'CREATE_COMMENT':
            post_id = action_args.get('post_id')
            if post_id:
                post_info = _get_post_info(cursor, post_id, agent_names)
                if post_info:
                    action_args['post_content'] = post_info.get('content', '')
                    action_args['post_author_name'] = post_info.get('author_name', '')
    
    except Exception as e:
        # A failure to fill in the context must not break the main flow
        print(f"Failed to enrich action context: {e}")


def _get_post_info(
    cursor,
    post_id: int,
    agent_names: Dict[int, str]
) -> Optional[Dict[str, str]]:
    """
    Look up a post.
    
    Args:
        cursor: Database cursor
        post_id: Post ID
        agent_names: The agent_id -> agent_name mapping
        
    Returns:
        A dict of content and author_name, or None
    """
    try:
        cursor.execute("""
            SELECT p.content, p.user_id, u.agent_id
            FROM post p
            LEFT JOIN user u ON p.user_id = u.user_id
            WHERE p.post_id = ?
        """, (post_id,))
        row = cursor.fetchone()
        if row:
            content = row[0] or ''
            user_id = row[1]
            agent_id = row[2]
            
            # Prefer the name from agent_names
            author_name = ''
            if agent_id is not None and agent_id in agent_names:
                author_name = agent_names[agent_id]
            elif user_id:
                # Fall back to the user table
                cursor.execute("SELECT name, user_name FROM user WHERE user_id = ?", (user_id,))
                user_row = cursor.fetchone()
                if user_row:
                    author_name = user_row[0] or user_row[1] or ''
            
            return {'content': content, 'author_name': author_name}
    except Exception:
        pass
    return None


def _get_user_name(
    cursor,
    user_id: int,
    agent_names: Dict[int, str]
) -> Optional[str]:
    """
    Look up a user name.
    
    Args:
        cursor: Database cursor
        user_id: User ID
        agent_names: The agent_id -> agent_name mapping
        
    Returns:
        The user name, or None
    """
    try:
        cursor.execute("""
            SELECT agent_id, name, user_name FROM user WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        if row:
            agent_id = row[0]
            name = row[1]
            user_name = row[2]
            
            # Prefer the name from agent_names
            if agent_id is not None and agent_id in agent_names:
                return agent_names[agent_id]
            return name or user_name or ''
    except Exception:
        pass
    return None


def _get_comment_info(
    cursor,
    comment_id: int,
    agent_names: Dict[int, str]
) -> Optional[Dict[str, str]]:
    """
    Look up a comment.
    
    Args:
        cursor: Database cursor
        comment_id: Comment ID
        agent_names: The agent_id -> agent_name mapping
        
    Returns:
        A dict of content and author_name, or None
    """
    try:
        cursor.execute("""
            SELECT c.content, c.user_id, u.agent_id
            FROM comment c
            LEFT JOIN user u ON c.user_id = u.user_id
            WHERE c.comment_id = ?
        """, (comment_id,))
        row = cursor.fetchone()
        if row:
            content = row[0] or ''
            user_id = row[1]
            agent_id = row[2]
            
            # Prefer the name from agent_names
            author_name = ''
            if agent_id is not None and agent_id in agent_names:
                author_name = agent_names[agent_id]
            elif user_id:
                # Fall back to the user table
                cursor.execute("SELECT name, user_name FROM user WHERE user_id = ?", (user_id,))
                user_row = cursor.fetchone()
                if user_row:
                    author_name = user_row[0] or user_row[1] or ''
            
            return {'content': content, 'author_name': author_name}
    except Exception:
        pass
    return None


def create_model(config: Dict[str, Any], use_boost: bool = False, label: str = "simulation"):
    """
    Create the LLM model.

    Three LLM configurations are supported:
    - General: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME
    - Simulation (optional): SIMULATION_LLM_API_KEY, SIMULATION_LLM_BASE_URL,
      SIMULATION_LLM_MODEL_NAME
    - Boost (optional): LLM_BOOST_API_KEY, LLM_BOOST_BASE_URL, LLM_BOOST_MODEL_NAME

    The simulation configuration exists because the agent loop is the pipeline's
    dominant cost by a wide margin - rounds x active agents x platforms, against
    tens of calls for everything else - while being the step that needs the least
    of the model: pick one action from a listed action space. A cheap model here
    and a stronger one for ontology, profiles and the report is a large saving
    for little quality lost. Unset, everything runs on the general LLM as before.

    With a boost LLM configured, the two platforms can run against different
    API providers, which raises the concurrency ceiling. Boost still wins over
    the simulation configuration on the platform that asks for it.

    Args:
        config: The simulation config
        use_boost: Use the boost LLM configuration when it is available
    """
    from app.config import Config, resolve_llm_api_key

    # Is a boost configuration present?
    boost_base_url = os.environ.get("LLM_BOOST_BASE_URL", "")
    boost_api_key = resolve_llm_api_key(os.environ.get("LLM_BOOST_API_KEY"), boost_base_url) or ""
    boost_model = os.environ.get("LLM_BOOST_MODEL_NAME", "")
    has_boost_config = bool(boost_api_key)

    # Pick the LLM from the argument and what is configured
    if use_boost and has_boost_config:
        # Use the boost configuration
        llm_api_key = boost_api_key
        llm_base_url = boost_base_url
        llm_model = boost_model or Config.LLM_MODEL_NAME
        config_label = "[boost LLM]"
    else:
        # SIMULATION_LLM_* where set, LLM_* otherwise - Config resolves that,
        # including the rule that a key never travels to another endpoint.
        llm_base_url = Config.SIMULATION_LLM_BASE_URL or ""
        llm_api_key = Config.SIMULATION_LLM_API_KEY or ""
        llm_model = Config.SIMULATION_LLM_MODEL_NAME or ""
        config_label = (
            "[simulation LLM]"
            if llm_model != Config.LLM_MODEL_NAME or llm_base_url != Config.LLM_BASE_URL
            else "[general LLM]"
        )

    # No model name in .env: fall back to the config file
    if not llm_model:
        llm_model = config.get("llm_model", "gpt-4o-mini")
    
    # Set the environment variables camel-ai expects
    if llm_api_key:
        os.environ["OPENAI_API_KEY"] = llm_api_key
    
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError(
            "No API key configured. Set LLM_API_KEY in the .env file at the project root "
            "(not needed when LLM_BASE_URL points at a local server)."
        )
    
    if llm_base_url:
        os.environ["OPENAI_API_BASE_URL"] = llm_base_url
    
    print(f"{config_label} model={llm_model}, base_url={llm_base_url[:40] if llm_base_url else 'default'}...")
    
    # Agent steps and interviews run here, not in the Flask process, so this is
    # the only place their token spend can be counted.
    return instrument_model(
        ModelFactory.create(
            model_platform=ModelPlatformType.OPENAI,
            model_type=llm_model,
        ),
        label,
    )


def warn_if_no_agent_activity(
    log_info,
    total_rounds: int,
    agent_activations: int,
    total_actions: int,
    seeded_actions: int,
) -> None:
    """
    Shout when a run completed without the agents doing anything.

    A run where every round finds zero active agents finishes in seconds,
    reports `completed`, and leaves only the seeded posts behind - it looks
    like a successful simulation of a silent audience. It is almost always a
    config-shape problem instead, so name the usual suspects here rather than
    leaving it to be spotted in the post count days later.
    """
    if agent_activations > 0:
        return

    log_info("=" * 60)
    log_info(
        f"WARNING: {total_rounds} rounds ran and no agent was ever activated. "
        f"The only actions logged are the {seeded_actions} seeded post(s) "
        f"(total actions: {total_actions})."
    )
    log_info("Usual causes, in order:")
    log_info(
        "  1. agent_configs[].active_hours does not overlap the simulated "
        "clock, or is not hour-of-day integers"
    )
    log_info("  2. time_config.agents_per_hour_min/max are zero or negative")
    log_info("  3. agent_configs[].activity_level is zero for every agent")
    log_info("  4. agent ids in the config do not exist in the agent graph")
    log_info("=" * 60)


def log_token_usage(log_manager, simulation_dir: str, stage: str) -> None:
    """
    Report what this process has spent so far and persist it.

    Interviews arrive after the simulation loop has finished, so the totals are
    written at both points rather than only at exit - a run killed in
    command-wait mode still leaves its accounting behind.
    """
    snapshot = TOKEN_METER.snapshot()
    total = snapshot["total"]
    if not total["calls"]:
        return

    log_manager.info(
        f"LLM tokens after {stage}: calls={total['calls']} "
        f"prompt={total['prompt_tokens']} completion={total['completion_tokens']}"
    )
    for label, bucket in sorted(snapshot["by_label"].items()):
        log_manager.info(
            f"  - {label}: calls={bucket['calls']} "
            f"prompt={bucket['prompt_tokens']} completion={bucket['completion_tokens']}"
        )
    TOKEN_METER.write(simulation_dir)


def get_active_agents_for_round(
    env,
    config: Dict[str, Any],
    current_hour: int,
    round_num: int
) -> List:
    """Decide which agents are active this round, from the clock and the config."""
    time_config = config.get("time_config", {})
    agent_configs = config.get("agent_configs", [])
    
    base_min = time_config.get("agents_per_hour_min", 5)
    base_max = time_config.get("agents_per_hour_max", 20)
    
    peak_hours = time_config.get("peak_hours", [9, 10, 11, 14, 15, 20, 21, 22])
    off_peak_hours = time_config.get("off_peak_hours", [0, 1, 2, 3, 4, 5])
    
    if current_hour in peak_hours:
        multiplier = time_config.get("peak_activity_multiplier", 1.5)
    elif current_hour in off_peak_hours:
        multiplier = time_config.get("off_peak_activity_multiplier", 0.3)
    else:
        multiplier = 1.0
    
    target_count = int(random.uniform(base_min, base_max) * multiplier)
    
    candidates = []
    for cfg in agent_configs:
        agent_id = cfg.get("agent_id", 0)
        # The config is LLM-written, so this field arrives as ints, as "18:00"
        # strings or as a window. Compared raw against an int hour it matches
        # nothing and the run goes silent.
        active_hours = normalize_active_hours(cfg.get("active_hours"))
        activity_level = cfg.get("activity_level", 0.5)

        if current_hour not in active_hours:
            continue
        
        if random.random() < activity_level:
            candidates.append(agent_id)
    
    selected_ids = random.sample(
        candidates, 
        min(target_count, len(candidates))
    ) if candidates else []
    
    active_agents = []
    lookup_errors = []
    for agent_id in selected_ids:
        try:
            agent = env.agent_graph.get_agent(agent_id)
            active_agents.append((agent_id, agent))
        except Exception as e:
            # Swallowing this silently turns a broken id mapping into a run
            # that completes with no activity and no explanation.
            lookup_errors.append(f"{agent_id}: {type(e).__name__}")

    if lookup_errors:
        logging.getLogger(__name__).warning(
            f"round {round_num}: {len(lookup_errors)} of {len(selected_ids)} "
            f"agent lookups failed ({', '.join(lookup_errors[:3])}"
            f"{', ...' if len(lookup_errors) > 3 else ''})"
        )

    return active_agents


class PlatformSimulation:
    """Holds the result of one platform's simulation."""
    def __init__(self):
        self.env = None
        self.agent_graph = None
        self.total_actions = 0


async def run_twitter_simulation(
    config: Dict[str, Any], 
    simulation_dir: str,
    action_logger: Optional[PlatformActionLogger] = None,
    main_logger: Optional[SimulationLogManager] = None,
    max_rounds: Optional[int] = None
) -> PlatformSimulation:
    """Run the Twitter simulation.
    
    Args:
        config: The simulation config
        simulation_dir: The simulation directory
        action_logger: The action logger
        main_logger: The main log manager
        max_rounds: Round cap (optional; truncates an over-long run)
        
    Returns:
        PlatformSimulation, carrying the env and the agent_graph
    """
    result = PlatformSimulation()
    
    def log_info(msg):
        if main_logger:
            main_logger.info(f"[Twitter] {msg}")
        print(f"[Twitter] {msg}")
    
    log_info("Initialising...")
    
    # Twitter uses the general LLM configuration
    model = create_model(config, use_boost=False, label="twitter")
    
    # OASIS Twitter wants CSV
    profile_path = os.path.join(simulation_dir, "twitter_profiles.csv")
    if not os.path.exists(profile_path):
        log_info(f"Error: profile file does not exist: {profile_path}")
        return result
    
    result.agent_graph = await generate_twitter_agent_graph(
        profile_path=profile_path,
        model=model,
        available_actions=TWITTER_ACTIONS,
    )
    
    # Read the real agent names from the config (entity_name, not the default Agent_X)
    agent_names = get_agent_names_from_config(config)
    # An agent missing from the config keeps the OASIS default name
    for agent_id, agent in result.agent_graph.get_agents():
        if agent_id not in agent_names:
            agent_names[agent_id] = getattr(agent, 'name', f'Agent_{agent_id}')
    
    db_path = os.path.join(simulation_dir, "twitter_simulation.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    
    result.env = oasis.make(
        agent_graph=result.agent_graph,
        platform=oasis.DefaultPlatformType.TWITTER,
        database_path=db_path,
        semaphore=30,  # Cap concurrent LLM requests so the API is not overloaded
    )
    
    await result.env.reset()
    log_info("Environment started")
    
    if action_logger:
        action_logger.log_simulation_start(config)
    
    total_actions = 0
    last_rowid = 0  # Highest database row processed; rowid avoids the created_at format difference
    
    # Run the initial events
    event_config = config.get("event_config", {})
    initial_posts = event_config.get("initial_posts", [])
    
    # Log the start of round 0, the initial-event phase
    if action_logger:
        action_logger.log_round_start(0, 0)  # round 0, simulated_hour 0
    
    initial_action_count = 0
    if initial_posts:
        initial_actions = {}
        for post in initial_posts:
            agent_id = post.get("poster_agent_id", 0)
            content = post.get("content", "")
            try:
                agent = result.env.agent_graph.get_agent(agent_id)
                initial_actions[agent] = ManualAction(
                    action_type=ActionType.CREATE_POST,
                    action_args={"content": content}
                )
                
                if action_logger:
                    action_logger.log_action(
                        round_num=0,
                        agent_id=agent_id,
                        agent_name=agent_names.get(agent_id, f"Agent_{agent_id}"),
                        action_type="CREATE_POST",
                        action_args={"content": content}
                    )
                    total_actions += 1
                    initial_action_count += 1
            except Exception:
                pass
        
        if initial_actions:
            await result.env.step(initial_actions)
            log_info(f"Published {len(initial_actions)} initial posts")
    
    # Log the end of round 0
    if action_logger:
        action_logger.log_round_end(0, initial_action_count, 0)
    
    # Main simulation loop
    time_config = config.get("time_config", {})
    total_hours = time_config.get("total_simulation_hours", 72)
    minutes_per_round = time_config.get("minutes_per_round", 30)
    total_rounds = (total_hours * 60) // minutes_per_round
    
    # Truncate when a round cap was given
    if max_rounds is not None and max_rounds > 0:
        original_rounds = total_rounds
        total_rounds = min(total_rounds, max_rounds)
        if total_rounds < original_rounds:
            log_info(f"Round count truncated: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")
    
    start_time = datetime.now()
    agent_activations = 0

    for round_num in range(total_rounds):
        # Has a shutdown signal arrived?
        if _shutdown_event and _shutdown_event.is_set():
            if main_logger:
                main_logger.info(f"Received a shutdown signal; stopping at round {round_num + 1}")
            break
        
        simulated_minutes = round_num * minutes_per_round
        simulated_hour = (simulated_minutes // 60) % 24
        simulated_day = simulated_minutes // (60 * 24) + 1
        
        active_agents = get_active_agents_for_round(
            result.env, config, simulated_hour, round_num
        )
        
        # Log the round start whether or not any agent is active
        if action_logger:
            action_logger.log_round_start(round_num + 1, simulated_hour)
        
        if not active_agents:
            # With no active agent, still log the round end (actions_count=0)
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0, simulated_hour)
            continue
        
        actions = {agent: LLMAction() for _, agent in active_agents}
        agent_activations += len(actions)
        await result.env.step(actions)
        
        # Read the actions that actually ran out of the database and log them
        actual_actions, last_rowid = fetch_new_actions_from_db(
            db_path, last_rowid, agent_names
        )
        
        round_action_count = 0
        for action_data in actual_actions:
            if action_logger:
                action_logger.log_action(
                    round_num=round_num + 1,
                    agent_id=action_data['agent_id'],
                    agent_name=action_data['agent_name'],
                    action_type=action_data['action_type'],
                    action_args=action_data['action_args']
                )
                total_actions += 1
                round_action_count += 1
        
        if action_logger:
            action_logger.log_round_end(round_num + 1, round_action_count, simulated_hour)
        
        if (round_num + 1) % 20 == 0:
            progress = (round_num + 1) / total_rounds * 100
            log_info(f"Day {simulated_day}, {simulated_hour:02d}:00 - Round {round_num + 1}/{total_rounds} ({progress:.1f}%)")
    
    # The environment is deliberately left open, for interviews
    
    if action_logger:
        action_logger.log_simulation_end(total_rounds, total_actions)
    
    result.total_actions = total_actions
    elapsed = (datetime.now() - start_time).total_seconds()
    log_info(f"Simulation loop complete. Elapsed: {elapsed:.1f}s, total actions: {total_actions}")
    warn_if_no_agent_activity(
        log_info, total_rounds, agent_activations, total_actions, initial_action_count
    )

    return result


async def run_reddit_simulation(
    config: Dict[str, Any], 
    simulation_dir: str,
    action_logger: Optional[PlatformActionLogger] = None,
    main_logger: Optional[SimulationLogManager] = None,
    max_rounds: Optional[int] = None
) -> PlatformSimulation:
    """Run the Reddit simulation.
    
    Args:
        config: The simulation config
        simulation_dir: The simulation directory
        action_logger: The action logger
        main_logger: The main log manager
        max_rounds: Round cap (optional; truncates an over-long run)
        
    Returns:
        PlatformSimulation, carrying the env and the agent_graph
    """
    result = PlatformSimulation()
    
    def log_info(msg):
        if main_logger:
            main_logger.info(f"[Reddit] {msg}")
        print(f"[Reddit] {msg}")
    
    log_info("Initialising...")
    
    # Reddit uses the boost LLM configuration when available, else the general one
    model = create_model(config, use_boost=True, label="reddit")
    
    profile_path = os.path.join(simulation_dir, "reddit_profiles.json")
    if not os.path.exists(profile_path):
        log_info(f"Error: profile file does not exist: {profile_path}")
        return result
    
    result.agent_graph = await generate_reddit_agent_graph(
        profile_path=profile_path,
        model=model,
        available_actions=REDDIT_ACTIONS,
    )
    
    # Read the real agent names from the config (entity_name, not the default Agent_X)
    agent_names = get_agent_names_from_config(config)
    # An agent missing from the config keeps the OASIS default name
    for agent_id, agent in result.agent_graph.get_agents():
        if agent_id not in agent_names:
            agent_names[agent_id] = getattr(agent, 'name', f'Agent_{agent_id}')
    
    db_path = os.path.join(simulation_dir, "reddit_simulation.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    
    result.env = oasis.make(
        agent_graph=result.agent_graph,
        platform=oasis.DefaultPlatformType.REDDIT,
        database_path=db_path,
        semaphore=30,  # Cap concurrent LLM requests so the API is not overloaded
    )
    
    await result.env.reset()
    log_info("Environment started")
    
    if action_logger:
        action_logger.log_simulation_start(config)
    
    total_actions = 0
    last_rowid = 0  # Highest database row processed; rowid avoids the created_at format difference
    
    # Run the initial events
    event_config = config.get("event_config", {})
    initial_posts = event_config.get("initial_posts", [])
    
    # Log the start of round 0, the initial-event phase
    if action_logger:
        action_logger.log_round_start(0, 0)  # round 0, simulated_hour 0
    
    initial_action_count = 0
    if initial_posts:
        initial_actions = {}
        for post in initial_posts:
            agent_id = post.get("poster_agent_id", 0)
            content = post.get("content", "")
            try:
                agent = result.env.agent_graph.get_agent(agent_id)
                if agent in initial_actions:
                    if not isinstance(initial_actions[agent], list):
                        initial_actions[agent] = [initial_actions[agent]]
                    initial_actions[agent].append(ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    ))
                else:
                    initial_actions[agent] = ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    )
                
                if action_logger:
                    action_logger.log_action(
                        round_num=0,
                        agent_id=agent_id,
                        agent_name=agent_names.get(agent_id, f"Agent_{agent_id}"),
                        action_type="CREATE_POST",
                        action_args={"content": content}
                    )
                    total_actions += 1
                    initial_action_count += 1
            except Exception:
                pass
        
        if initial_actions:
            await result.env.step(initial_actions)
            log_info(f"Published {len(initial_actions)} initial posts")
    
    # Log the end of round 0
    if action_logger:
        action_logger.log_round_end(0, initial_action_count, 0)
    
    # Main simulation loop
    time_config = config.get("time_config", {})
    total_hours = time_config.get("total_simulation_hours", 72)
    minutes_per_round = time_config.get("minutes_per_round", 30)
    total_rounds = (total_hours * 60) // minutes_per_round
    
    # Truncate when a round cap was given
    if max_rounds is not None and max_rounds > 0:
        original_rounds = total_rounds
        total_rounds = min(total_rounds, max_rounds)
        if total_rounds < original_rounds:
            log_info(f"Round count truncated: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")
    
    start_time = datetime.now()
    agent_activations = 0

    for round_num in range(total_rounds):
        # Has a shutdown signal arrived?
        if _shutdown_event and _shutdown_event.is_set():
            if main_logger:
                main_logger.info(f"Received a shutdown signal; stopping at round {round_num + 1}")
            break
        
        simulated_minutes = round_num * minutes_per_round
        simulated_hour = (simulated_minutes // 60) % 24
        simulated_day = simulated_minutes // (60 * 24) + 1
        
        active_agents = get_active_agents_for_round(
            result.env, config, simulated_hour, round_num
        )
        
        # Log the round start whether or not any agent is active
        if action_logger:
            action_logger.log_round_start(round_num + 1, simulated_hour)
        
        if not active_agents:
            # With no active agent, still log the round end (actions_count=0)
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0, simulated_hour)
            continue
        
        actions = {agent: LLMAction() for _, agent in active_agents}
        agent_activations += len(actions)
        await result.env.step(actions)
        
        # Read the actions that actually ran out of the database and log them
        actual_actions, last_rowid = fetch_new_actions_from_db(
            db_path, last_rowid, agent_names
        )
        
        round_action_count = 0
        for action_data in actual_actions:
            if action_logger:
                action_logger.log_action(
                    round_num=round_num + 1,
                    agent_id=action_data['agent_id'],
                    agent_name=action_data['agent_name'],
                    action_type=action_data['action_type'],
                    action_args=action_data['action_args']
                )
                total_actions += 1
                round_action_count += 1
        
        if action_logger:
            action_logger.log_round_end(round_num + 1, round_action_count, simulated_hour)
        
        if (round_num + 1) % 20 == 0:
            progress = (round_num + 1) / total_rounds * 100
            log_info(f"Day {simulated_day}, {simulated_hour:02d}:00 - Round {round_num + 1}/{total_rounds} ({progress:.1f}%)")
    
    # The environment is deliberately left open, for interviews
    
    if action_logger:
        action_logger.log_simulation_end(total_rounds, total_actions)
    
    result.total_actions = total_actions
    elapsed = (datetime.now() - start_time).total_seconds()
    log_info(f"Simulation loop complete. Elapsed: {elapsed:.1f}s, total actions: {total_actions}")
    warn_if_no_agent_activity(
        log_info, total_rounds, agent_activations, total_actions, initial_action_count
    )

    return result


async def main():
    parser = argparse.ArgumentParser(description='OASIS dual-platform parallel simulation')
    parser.add_argument(
        '--config', 
        type=str, 
        required=True,
        help='path to simulation_config.json'
    )
    parser.add_argument(
        '--twitter-only',
        action='store_true',
        help='run the Twitter simulation only'
    )
    parser.add_argument(
        '--reddit-only',
        action='store_true',
        help='run the Reddit simulation only'
    )
    parser.add_argument(
        '--max-rounds',
        type=int,
        default=None,
        help='round cap (optional; truncates an over-long run)'
    )
    parser.add_argument(
        '--no-wait',
        action='store_true',
        default=False,
        help='shut the environment down as soon as the run finishes, skipping command-wait mode'
    )
    parser.add_argument(
        '--idle-timeout',
        type=float,
        default=DEFAULT_IDLE_TIMEOUT,
        help=(
            'seconds to stay in command-wait mode with no IPC command before '
            'closing the environment; 0 disables the timeout and waits forever '
            f'(default: {DEFAULT_IDLE_TIMEOUT:.0f})'
        )
    )
    
    args = parser.parse_args()
    
    # Create the shutdown event as main() starts, so the whole program can
    # respond to a termination signal
    global _shutdown_event
    _shutdown_event = asyncio.Event()
    
    if not os.path.exists(args.config):
        print(f"Error: config file does not exist: {args.config}")
        sys.exit(1)
    
    config = load_config(args.config)
    simulation_dir = os.path.dirname(args.config) or "."
    wait_for_commands = not args.no_wait
    
    # Set up logging: silence OASIS, remove the previous files
    init_logging_for_simulation(simulation_dir)
    
    # Create the log manager
    log_manager = SimulationLogManager(simulation_dir)
    twitter_logger = log_manager.get_twitter_logger()
    reddit_logger = log_manager.get_reddit_logger()
    
    log_manager.info("=" * 60)
    log_manager.info("OASIS dual-platform parallel simulation")
    log_manager.info(f"Config file: {args.config}")
    log_manager.info(f"Simulation id: {config.get('simulation_id', 'unknown')}")
    log_manager.info(f"Wait-for-command mode: {'enabled' if wait_for_commands else 'disabled'}")
    log_manager.info("=" * 60)
    
    time_config = config.get("time_config", {})
    total_hours = time_config.get('total_simulation_hours', 72)
    minutes_per_round = time_config.get('minutes_per_round', 30)
    config_total_rounds = (total_hours * 60) // minutes_per_round
    
    log_manager.info(f"Simulation parameters:")
    log_manager.info(f"  - Total simulated duration: {total_hours}h")
    log_manager.info(f"  - Minutes per round: {minutes_per_round}")
    log_manager.info(f"  - Configured total rounds: {config_total_rounds}")
    if args.max_rounds:
        log_manager.info(f"  - Max rounds: {args.max_rounds}")
        if args.max_rounds < config_total_rounds:
            log_manager.info(f"  - Rounds actually run: {args.max_rounds} (truncated)")
    log_manager.info(f"  - Agent count: {len(config.get('agent_configs', []))}")
    
    log_manager.info("Log layout:")
    log_manager.info(f"  - Main log: simulation.log")
    log_manager.info(f"  - Twitter actions: twitter/actions.jsonl")
    log_manager.info(f"  - Reddit actions: reddit/actions.jsonl")
    log_manager.info("=" * 60)
    
    start_time = datetime.now()
    
    # Results for both platforms
    twitter_result: Optional[PlatformSimulation] = None
    reddit_result: Optional[PlatformSimulation] = None
    
    if args.twitter_only:
        twitter_result = await run_twitter_simulation(config, simulation_dir, twitter_logger, log_manager, args.max_rounds)
    elif args.reddit_only:
        reddit_result = await run_reddit_simulation(config, simulation_dir, reddit_logger, log_manager, args.max_rounds)
    else:
        # Run in parallel, each platform with its own logger
        results = await asyncio.gather(
            run_twitter_simulation(config, simulation_dir, twitter_logger, log_manager, args.max_rounds),
            run_reddit_simulation(config, simulation_dir, reddit_logger, log_manager, args.max_rounds),
        )
        twitter_result, reddit_result = results
    
    total_elapsed = (datetime.now() - start_time).total_seconds()
    log_manager.info("=" * 60)
    log_manager.info(f"Simulation loop complete. Total elapsed: {total_elapsed:.1f}s")
    log_token_usage(log_manager, simulation_dir, "simulation loop")

    # Enter command-wait mode?
    if wait_for_commands:
        log_manager.info("")
        log_manager.info("=" * 60)
        idle_timeout = max(0.0, args.idle_timeout)
        log_manager.info("Entering wait-for-command mode - the environment stays up")
        log_manager.info("Supported commands: interview, batch_interview, close_env")
        if idle_timeout:
            log_manager.info(f"Idle timeout: {idle_timeout:.0f}s with no command closes the environment")
        else:
            log_manager.info("Idle timeout: disabled - waiting for close_env")
        log_manager.info("=" * 60)
        
        # Create the IPC handler
        ipc_handler = ParallelIPCHandler(
            simulation_dir=simulation_dir,
            twitter_env=twitter_result.env if twitter_result else None,
            twitter_agent_graph=twitter_result.agent_graph if twitter_result else None,
            reddit_env=reddit_result.env if reddit_result else None,
            reddit_agent_graph=reddit_result.agent_graph if reddit_result else None
        )
        ipc_handler.update_status("alive")
        
        # Command-wait loop, driven by the global _shutdown_event
        try:
            while not _shutdown_event.is_set():
                should_continue = await ipc_handler.process_commands()
                if not should_continue:
                    break
                # An idle environment closes itself. process_commands() refreshes
                # last_command_at on every serviced command, so an active
                # interview session keeps pushing the deadline out.
                if idle_timeout:
                    idle_for = time.monotonic() - ipc_handler.last_command_at
                    if idle_for >= idle_timeout:
                        log_manager.info(
                            f"\nNo command for {idle_for:.0f}s - closing the environment on idle timeout"
                        )
                        break
                # wait_for instead of sleep, so shutdown_event can interrupt it
                try:
                    await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)
                    break  # Shutdown signal received
                except asyncio.TimeoutError:
                    pass  # Timed out; keep looping
        except KeyboardInterrupt:
            print("\nReceived interrupt")
        except asyncio.CancelledError:
            print("\nTask cancelled")
        except Exception as e:
            print(f"\nCommand handling failed: {e}")
        
        log_manager.info("\nClosing the environment...")
        ipc_handler.update_status("stopped")
    
    # Shut the environments down
    if twitter_result and twitter_result.env:
        await twitter_result.env.close()
        log_manager.info("[Twitter] Environment closed")
    
    if reddit_result and reddit_result.env:
        await reddit_result.env.close()
        log_manager.info("[Reddit] Environment closed")
    
    log_token_usage(log_manager, simulation_dir, "whole process")

    log_manager.info("=" * 60)
    log_manager.info(f"All done.")
    log_manager.info(f"Log files:")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'simulation.log')}")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'twitter', 'actions.jsonl')}")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'reddit', 'actions.jsonl')}")
    log_manager.info("=" * 60)


def setup_signal_handlers(loop=None):
    """
    Install signal handlers so SIGTERM/SIGINT shut the program down cleanly,
    
    This is a persistent simulation: it does not exit when the run finishes,
    it waits for interview commands. On a termination signal it must:
    1. Tell the asyncio loop to stop waiting
    2. Give the program a chance to release its resources (database, environment, ...)
    3. Only then exit
    """
    def signal_handler(signum, frame):
        global _cleanup_done
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"\nReceived {sig_name}, exiting...")
        
        if not _cleanup_done:
            _cleanup_done = True
            # Set the event so the asyncio loop unwinds and can clean up
            if _shutdown_event:
                _shutdown_event.set()
        
        # Do not call sys.exit() here: let the asyncio loop exit and clean up.
        # Only a repeat signal forces an exit
        else:
            print("Forcing exit...")
            sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


if __name__ == "__main__":
    setup_signal_handlers()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
    except SystemExit:
        pass
    finally:
        # Clean up the multiprocessing resource tracker, to avoid a warning on exit
        try:
            from multiprocessing import resource_tracker
            resource_tracker._resource_tracker._stop()
        except Exception:
            pass
        print("Simulation process exited")
