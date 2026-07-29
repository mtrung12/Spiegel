"""
Preset script for the OASIS Twitter simulation.
The script reads its parameters from the config file and runs the whole
simulation unattended.

Features:
- Keeps the environment alive after the run and waits for commands
- Accepts interview commands over IPC
- Supports single-agent and batch interviews
- Supports a remote shutdown command

Usage:
    python run_twitter_simulation.py --config /path/to/simulation_config.json
    python run_twitter_simulation.py --config /path/to/simulation_config.json --no-wait  # close as soon as it finishes
"""

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import sys
import sqlite3
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

# Global, used by the signal handlers
_shutdown_event = None
_cleanup_done = False

# Add the project to sys.path
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
_project_root = os.path.abspath(os.path.join(_backend_dir, '..'))
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _backend_dir)

from agent_activity import normalize_active_hours

# Load the .env file from the project root (LLM_API_KEY and friends)
from dotenv import load_dotenv
_env_file = os.path.join(_project_root, '.env')
if os.path.exists(_env_file):
    load_dotenv(_env_file)
else:
    _backend_env = os.path.join(_backend_dir, '.env')
    if os.path.exists(_backend_env):
        load_dotenv(_backend_env)


import re


class UnicodeFormatter(logging.Formatter):
    """Formatter that turns Unicode escape sequences back into readable characters."""
    
    UNICODE_ESCAPE_PATTERN = re.compile(r'\\u([0-9a-fA-F]{4})')
    
    def format(self, record):
        result = super().format(record)
        
        def replace_unicode(match):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return match.group(0)
        
        return self.UNICODE_ESCAPE_PATTERN.sub(replace_unicode, result)


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


def setup_oasis_logging(log_dir: str):
    """Configure OASIS logging, writing to a fixed log file name."""
    os.makedirs(log_dir, exist_ok=True)
    
    # Remove the previous log file
    for f in os.listdir(log_dir):
        old_log = os.path.join(log_dir, f)
        if os.path.isfile(old_log) and f.endswith('.log'):
            try:
                os.remove(old_log)
            except OSError:
                pass
    
    formatter = UnicodeFormatter("%(levelname)s - %(asctime)s - %(name)s - %(message)s")
    
    loggers_config = {
        "social.agent": os.path.join(log_dir, "social.agent.log"),
        "social.twitter": os.path.join(log_dir, "social.twitter.log"),
        "social.rec": os.path.join(log_dir, "social.rec.log"),
        "oasis.env": os.path.join(log_dir, "oasis.env.log"),
        "table": os.path.join(log_dir, "table.log"),
    }
    
    for logger_name, log_file in loggers_config.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='w')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.propagate = False


try:
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    import oasis
    from oasis import (
        ActionType,
        LLMAction,
        ManualAction,
        generate_twitter_agent_graph
    )
except ImportError as e:
    print(f"Error: missing dependency {e}")
    print("Install it first: pip install oasis-ai camel-ai")
    sys.exit(1)


# IPC constants
IPC_COMMANDS_DIR = "ipc_commands"
IPC_RESPONSES_DIR = "ipc_responses"
ENV_STATUS_FILE = "env_status.json"

# How long command-wait mode stays up with no IPC command before closing the
# environment itself. Without it a run whose client went away never exits, so
# SimulationRunner's monitor never publishes a terminal status.
DEFAULT_IDLE_TIMEOUT = 3600.0

class CommandType:
    """Command type constants."""
    INTERVIEW = "interview"
    BATCH_INTERVIEW = "batch_interview"
    CLOSE_ENV = "close_env"


class IPCHandler:
    """IPC command handler."""
    
    def __init__(self, simulation_dir: str, env, agent_graph):
        self.simulation_dir = simulation_dir
        self.env = env
        self.agent_graph = agent_graph
        self.commands_dir = os.path.join(simulation_dir, IPC_COMMANDS_DIR)
        self.responses_dir = os.path.join(simulation_dir, IPC_RESPONSES_DIR)
        self.status_file = os.path.join(simulation_dir, ENV_STATUS_FILE)
        self._running = True

        # Monotonic timestamp of the last serviced command; drives the idle
        # timeout in the command-wait loop.
        self.last_command_at = time.monotonic()
        
        # Make sure the directories exist
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)
    
    def update_status(self, status: str):
        """Update the environment status."""
        with open(self.status_file, 'w', encoding='utf-8') as f:
            json.dump({
                "status": status,
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
    
    async def handle_interview(self, command_id: str, agent_id: int, prompt: str) -> bool:
        """
        Handle a single-agent interview command.
        
        Returns:
            True on success, False on failure
        """
        try:
            # Look up the agent
            agent = self.agent_graph.get_agent(agent_id)
            
            # Build the interview action
            interview_action = ManualAction(
                action_type=ActionType.INTERVIEW,
                action_args={"prompt": prompt}
            )
            
            # Run the interview
            actions = {agent: interview_action}
            await self.env.step(actions)
            
            # Read the result out of the database
            result = self._get_interview_result(agent_id)
            
            self.send_response(command_id, "completed", result=result)
            print(f"  Interview complete: agent_id={agent_id}")
            return True
            
        except Exception as e:
            error_msg = str(e)
            print(f"  Interview failed: agent_id={agent_id}, error={error_msg}")
            self.send_response(command_id, "failed", error=error_msg)
            return False
    
    async def handle_batch_interview(self, command_id: str, interviews: List[Dict]) -> bool:
        """
        Handle a batch interview command.
        
        Args:
            interviews: [{"agent_id": int, "prompt": str}, ...]
        """
        try:
            # Build the action dict
            actions = {}
            agent_prompts = {}  # The prompt used per agent
            
            for interview in interviews:
                agent_id = interview.get("agent_id")
                prompt = interview.get("prompt", "")
                
                try:
                    agent = self.agent_graph.get_agent(agent_id)
                    actions[agent] = ManualAction(
                        action_type=ActionType.INTERVIEW,
                        action_args={"prompt": prompt}
                    )
                    agent_prompts[agent_id] = prompt
                except Exception as e:
                    print(f"  Warning: cannot fetch agent {agent_id}: {e}")
            
            if not actions:
                self.send_response(command_id, "failed", error="no valid agents")
                return False
            
            # Run the batch interview
            await self.env.step(actions)
            
            # Collect every result
            results = {}
            for agent_id in agent_prompts.keys():
                result = self._get_interview_result(agent_id)
                results[agent_id] = result
            
            self.send_response(command_id, "completed", result={
                "interviews_count": len(results),
                "results": results
            })
            print(f"  Batch interview complete: {len(results)} agents")
            return True
            
        except Exception as e:
            error_msg = str(e)
            print(f"  Batch interview failed: {error_msg}")
            self.send_response(command_id, "failed", error=error_msg)
            return False
    
    def _get_interview_result(self, agent_id: int) -> Dict[str, Any]:
        """Read the latest interview result out of the database."""
        db_path = os.path.join(self.simulation_dir, "twitter_simulation.db")
        
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
                args.get("prompt", "")
            )
            return True
            
        elif command_type == CommandType.BATCH_INTERVIEW:
            await self.handle_batch_interview(
                command_id,
                args.get("interviews", [])
            )
            return True
            
        elif command_type == CommandType.CLOSE_ENV:
            print("Received close-environment command")
            self.send_response(command_id, "completed", result={"message": "environment is shutting down"})
            return False
        
        else:
            self.send_response(command_id, "failed", error=f"unknown command type: {command_type}")
            return True


class TwitterSimulationRunner:
    """Twitter simulation runner."""
    
    # Actions available on Twitter. INTERVIEW is excluded: it can only be
    # triggered by hand, through a ManualAction.
    AVAILABLE_ACTIONS = [
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
    
    def __init__(
        self,
        config_path: str,
        wait_for_commands: bool = True,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    ):
        """
        Initialise the simulation runner.

        Args:
            config_path: Path to simulation_config.json
            wait_for_commands: Wait for commands once the run finishes (default True)
            idle_timeout: Seconds without a command before command-wait mode
                closes the environment; 0 waits forever
        """
        self.config_path = config_path
        self.config = self._load_config()
        self.simulation_dir = os.path.dirname(config_path)
        self.wait_for_commands = wait_for_commands
        self.idle_timeout = max(0.0, idle_timeout)
        self.env = None
        self.agent_graph = None
        self.ipc_handler = None
        
    def _load_config(self) -> Dict[str, Any]:
        """Load the config file."""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _get_profile_path(self) -> str:
        """Return the profile file path (OASIS Twitter wants CSV)."""
        return os.path.join(self.simulation_dir, "twitter_profiles.csv")
    
    def _get_db_path(self) -> str:
        """Return the database path."""
        return os.path.join(self.simulation_dir, "twitter_simulation.db")
    
    def _create_model(self):
        """
        Create the LLM model.
        
        Settings come from the .env file in the project root, which wins:
        - LLM_API_KEY: the API key (optional for a local LLM_BASE_URL)
        - LLM_BASE_URL: the API base URL
        - LLM_MODEL_NAME: the model name
        """
        from app.config import resolve_llm_api_key

        # Read from .env first
        llm_base_url = os.environ.get("LLM_BASE_URL", "")
        llm_api_key = resolve_llm_api_key(os.environ.get("LLM_API_KEY"), llm_base_url) or ""
        llm_model = os.environ.get("LLM_MODEL_NAME", "")
        
        # Fall back to the config file when .env has nothing
        if not llm_model:
            llm_model = self.config.get("llm_model", "gpt-4o-mini")
        
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
        
        print(f"LLM config: model={llm_model}, base_url={llm_base_url[:40] if llm_base_url else 'default'}...")
        
        return ModelFactory.create(
            model_platform=ModelPlatformType.OPENAI,
            model_type=llm_model,
        )
    
    def _get_active_agents_for_round(
        self, 
        env, 
        current_hour: int,
        round_num: int
    ) -> List:
        """
        Decide which agents are active this round, from the clock and the config.
        
        Args:
            env: The OASIS environment
            current_hour: Current simulated hour (0-23)
            round_num: Current round
            
        Returns:
            The activated agents
        """
        time_config = self.config.get("time_config", {})
        agent_configs = self.config.get("agent_configs", [])
        
        # Base number to activate
        base_min = time_config.get("agents_per_hour_min", 5)
        base_max = time_config.get("agents_per_hour_max", 20)
        
        # Adjust for the time band
        peak_hours = time_config.get("peak_hours", [9, 10, 11, 14, 15, 20, 21, 22])
        off_peak_hours = time_config.get("off_peak_hours", [0, 1, 2, 3, 4, 5])
        
        if current_hour in peak_hours:
            multiplier = time_config.get("peak_activity_multiplier", 1.5)
        elif current_hour in off_peak_hours:
            multiplier = time_config.get("off_peak_activity_multiplier", 0.3)
        else:
            multiplier = 1.0
        
        target_count = int(random.uniform(base_min, base_max) * multiplier)
        
        # Work out each agent's activation probability from its config
        candidates = []
        for cfg in agent_configs:
            agent_id = cfg.get("agent_id", 0)
            # The config is LLM-written, so this field arrives as ints, as
            # "18:00" strings or as a window. Compared raw against an int hour
            # it matches nothing and the run goes silent.
            active_hours = normalize_active_hours(cfg.get("active_hours"))
            activity_level = cfg.get("activity_level", 0.5)

            # Is the agent inside its active hours?
            if current_hour not in active_hours:
                continue
            
            # Derive the probability from the activity level
            if random.random() < activity_level:
                candidates.append(agent_id)
        
        # Sample
        selected_ids = random.sample(
            candidates, 
            min(target_count, len(candidates))
        ) if candidates else []
        
        # Resolve to agent objects
        active_agents = []
        for agent_id in selected_ids:
            try:
                agent = env.agent_graph.get_agent(agent_id)
                active_agents.append((agent_id, agent))
            except Exception:
                pass
        
        return active_agents
    
    async def run(self, max_rounds: int = None):
        """Run the Twitter simulation.
        
        Args:
            max_rounds: Round cap (optional; truncates an over-long run)
        """
        print("=" * 60)
        print("OASIS Twitter simulation")
        print(f"Config file: {self.config_path}")
        print(f"Simulation id: {self.config.get('simulation_id', 'unknown')}")
        print(f"Wait-for-command mode: {'enabled' if self.wait_for_commands else 'disabled'}")
        print("=" * 60)
        
        # Load the time configuration
        time_config = self.config.get("time_config", {})
        total_hours = time_config.get("total_simulation_hours", 72)
        minutes_per_round = time_config.get("minutes_per_round", 30)
        
        # Work out the total round count
        total_rounds = (total_hours * 60) // minutes_per_round
        
        # Truncate when a round cap was given
        if max_rounds is not None and max_rounds > 0:
            original_rounds = total_rounds
            total_rounds = min(total_rounds, max_rounds)
            if total_rounds < original_rounds:
                print(f"\nRound count truncated: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")
        
        print(f"\nSimulation parameters:")
        print(f"  - Total simulated duration: {total_hours}h")
        print(f"  - Minutes per round: {minutes_per_round}")
        print(f"  - Total rounds: {total_rounds}")
        if max_rounds:
            print(f"  - Max rounds: {max_rounds}")
        print(f"  - Agent count: {len(self.config.get('agent_configs', []))}")
        
        # Create the model
        print("\nInitialising the LLM model...")
        model = self._create_model()
        
        # Load the agent graph
        print("Loading agent profiles...")
        profile_path = self._get_profile_path()
        if not os.path.exists(profile_path):
            print(f"Error: profile file does not exist: {profile_path}")
            return
        
        self.agent_graph = await generate_twitter_agent_graph(
            profile_path=profile_path,
            model=model,
            available_actions=self.AVAILABLE_ACTIONS,
        )
        
        # Database path
        db_path = self._get_db_path()
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"Removed the old database: {db_path}")
        
        # Create the environment
        print("Creating the OASIS environment...")
        self.env = oasis.make(
            agent_graph=self.agent_graph,
            platform=oasis.DefaultPlatformType.TWITTER,
            database_path=db_path,
            semaphore=30,  # Cap concurrent LLM requests so the API is not overloaded
        )
        
        await self.env.reset()
        print("Environment initialised\n")
        
        # Start the IPC handler
        self.ipc_handler = IPCHandler(self.simulation_dir, self.env, self.agent_graph)
        self.ipc_handler.update_status("running")
        
        # Run the initial events
        event_config = self.config.get("event_config", {})
        initial_posts = event_config.get("initial_posts", [])
        
        if initial_posts:
            print(f"Running initial events ({len(initial_posts)} initial posts)...")
            initial_actions = {}
            for post in initial_posts:
                agent_id = post.get("poster_agent_id", 0)
                content = post.get("content", "")
                try:
                    agent = self.env.agent_graph.get_agent(agent_id)
                    initial_actions[agent] = ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    )
                except Exception as e:
                    print(f"  Warning: cannot create an initial post for agent {agent_id}: {e}")
            
            if initial_actions:
                await self.env.step(initial_actions)
                print(f"  Published {len(initial_actions)} initial posts")
        
        # Main simulation loop
        print("\nStarting the simulation loop...")
        start_time = datetime.now()
        
        for round_num in range(total_rounds):
            # Work out the current simulated time
            simulated_minutes = round_num * minutes_per_round
            simulated_hour = (simulated_minutes // 60) % 24
            simulated_day = simulated_minutes // (60 * 24) + 1
            
            # Which agents are active this round?
            active_agents = self._get_active_agents_for_round(
                self.env, simulated_hour, round_num
            )
            
            if not active_agents:
                continue
            
            # Build the actions
            actions = {
                agent: LLMAction()
                for _, agent in active_agents
            }
            
            # Run the actions
            await self.env.step(actions)
            
            # Report progress
            if (round_num + 1) % 10 == 0 or round_num == 0:
                elapsed = (datetime.now() - start_time).total_seconds()
                progress = (round_num + 1) / total_rounds * 100
                print(f"  [Day {simulated_day}, {simulated_hour:02d}:00] "
                      f"Round {round_num + 1}/{total_rounds} ({progress:.1f}%) "
                      f"- {len(active_agents)} agents active "
                      f"- elapsed: {elapsed:.1f}s")
        
        total_elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\nSimulation loop complete.")
        print(f"  - Total elapsed: {total_elapsed:.1f}s")
        print(f"  - Database: {db_path}")
        
        # Enter command-wait mode?
        if self.wait_for_commands:
            print("\n" + "=" * 60)
            print("Entering wait-for-command mode - the environment stays up")
            print("Supported commands: interview, batch_interview, close_env")
            if self.idle_timeout:
                print(f"Idle timeout: {self.idle_timeout:.0f}s with no command closes the environment")
            else:
                print("Idle timeout: disabled - waiting for close_env")
            print("=" * 60)

            self.ipc_handler.update_status("alive")

            # Command-wait loop, driven by the global _shutdown_event
            try:
                while not _shutdown_event.is_set():
                    should_continue = await self.ipc_handler.process_commands()
                    if not should_continue:
                        break
                    # An idle environment closes itself, so a run whose client
                    # went away still reaches a terminal status.
                    if self.idle_timeout:
                        idle_for = time.monotonic() - self.ipc_handler.last_command_at
                        if idle_for >= self.idle_timeout:
                            print(f"\nNo command for {idle_for:.0f}s - closing the environment on idle timeout")
                            break
                    try:
                        await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)
                        break  # Shutdown signal received
                    except asyncio.TimeoutError:
                        pass
            except KeyboardInterrupt:
                print("\nReceived interrupt")
            except asyncio.CancelledError:
                print("\nTask cancelled")
            except Exception as e:
                print(f"\nCommand handling failed: {e}")
            
            print("\nClosing the environment...")
        
        # Shut the environment down
        self.ipc_handler.update_status("stopped")
        await self.env.close()
        
        print("Environment closed")
        print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description='OASIS Twitter simulation')
    parser.add_argument(
        '--config', 
        type=str, 
        required=True,
        help='path to simulation_config.json'
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
    
    # Create the shutdown event as main() starts
    global _shutdown_event
    _shutdown_event = asyncio.Event()
    
    if not os.path.exists(args.config):
        print(f"Error: config file does not exist: {args.config}")
        sys.exit(1)
    
    # Set up logging: fixed file name, previous log removed
    simulation_dir = os.path.dirname(args.config) or "."
    setup_oasis_logging(os.path.join(simulation_dir, "log"))
    
    runner = TwitterSimulationRunner(
        config_path=args.config,
        wait_for_commands=not args.no_wait,
        idle_timeout=args.idle_timeout
    )
    await runner.run(max_rounds=args.max_rounds)


def setup_signal_handlers():
    """
    Install signal handlers so SIGTERM/SIGINT shut the program down cleanly,
    giving it a chance to release its resources (database, environment, ...).
    """
    def signal_handler(signum, frame):
        global _cleanup_done
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"\nReceived {sig_name}, exiting...")
        if not _cleanup_done:
            _cleanup_done = True
            if _shutdown_event:
                _shutdown_event.set()
        else:
            # Only a repeat signal forces an exit
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
        print("Simulation process exited")
