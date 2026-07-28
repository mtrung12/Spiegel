"""
Pipeline logging.

Two separate streams, because they answer two different questions:

1. ``actions.jsonl`` - what the pipeline *did*. One compact JSON line per step:
   which component, which action, on what target, how long it took, whether it
   succeeded. Model prose (prompts, agent replies, generated personas) never
   lands here, so the file stays readable as a timeline.

2. ``debug.jsonl`` - why it did that. One JSON line per step holding the full
   inputs, the input text, the intermediate notes, and the raw output text.
   Linked back to the action line through ``action_id``.

A human-readable mirror of the action stream is written to ``pipeline.log``.

Layout under the log root (default ``<repo>/local-doc/logs``)::

    local-doc/logs/
    ├── actions.jsonl              # every run, action stream only
    ├── debug.jsonl                # every run, full payloads
    ├── pipeline.log               # every run, human-readable timeline
    └── runs/<run_id>/
        ├── actions.jsonl          # same records, scoped to one run
        └── debug.jsonl

Environment:
    PIPELINE_LOG_DIR       Override the log root.
    PIPELINE_LOG_ENABLED   'false' disables every write. Default 'true'.
    PIPELINE_LOG_PAYLOADS  'false' keeps actions.jsonl but drops debug.jsonl.
    PIPELINE_LOG_MAX_TEXT  Per-field character cap in debug.jsonl. Default 20000.
    PIPELINE_LOG_MAX_BYTES Rotate a log file once it exceeds this. Default 50MB.

Usage::

    from ..utils.pipeline_logger import pipeline_log

    with pipeline_log.run(run_id=simulation_id, kind='simulation_prepare'):
        with pipeline_log.stage('generating_profiles'):
            with pipeline_log.step('OasisProfileGenerator', 'generate_profile',
                                   target=entity.name) as step:
                step.input(entity_type=entity_type, use_llm=True)
                step.input_text(context)
                ...
                step.output_text(raw_response)
                step.metric(persona_chars=len(persona))
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional


# ============ Configuration ============

def _repo_root() -> str:
    """Return the repository root (three levels above backend/app/utils)."""
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..')
    )


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {'0', 'false', 'no', 'off'}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


DEFAULT_LOG_DIR = os.path.join(_repo_root(), 'local-doc', 'logs')

# Keys whose values are replaced with a placeholder before anything is written.
# Deliberately does not match a bare "token": max_tokens and total_tokens are
# counts worth keeping, and every credential-shaped key is spelled out here.
_SECRET_KEY_PATTERN = re.compile(
    r'(api[_-]?key|secret|password|passwd|credential'
    r'|(access|auth|refresh|id|bearer|session)[_-]?token'
    r'|authorization|bearer)',
    re.IGNORECASE,
)
# Bare credentials that show up inside free text rather than as a dict value.
_SECRET_VALUE_PATTERN = re.compile(r'\b(sk-[A-Za-z0-9_\-]{12,}|z_[A-Za-z0-9_\-]{20,})')
_REDACTED = '<redacted>'


# ============ Ambient context ============

# Background threads start with an empty context, so a worker that runs on its
# own thread has to open its own run()/stage() scope. That is deliberate: it
# keeps a thread from silently inheriting a run id that has already finished.
_run_id: ContextVar[Optional[str]] = ContextVar('pipeline_run_id', default=None)
_run_kind: ContextVar[Optional[str]] = ContextVar('pipeline_run_kind', default=None)
_stage: ContextVar[Optional[str]] = ContextVar('pipeline_stage', default=None)


def _now() -> str:
    return datetime.now().isoformat(timespec='milliseconds')


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _redact(value: Any, _depth: int = 0) -> Any:
    """Strip credentials out of anything on its way to disk."""
    if _depth > 12:
        return '<max-depth>'
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_PATTERN.search(key):
                out[key] = _REDACTED
            else:
                out[key] = _redact(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact(item, _depth + 1) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE_PATTERN.sub(_REDACTED, value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _SECRET_VALUE_PATTERN.sub(_REDACTED, str(value))


def _jsonable(value: Any, _depth: int = 0) -> Any:
    """Coerce arbitrary objects into something json.dumps accepts."""
    if _depth > 12:
        return '<max-depth>'
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, _depth + 1) for v in value]
    if hasattr(value, 'to_dict'):
        try:
            return _jsonable(value.to_dict(), _depth + 1)
        except Exception:
            pass
    return repr(value)


class PipelineLogger:
    """Writer for the action and debug streams. Safe to share across threads."""

    def __init__(self, log_dir: Optional[str] = None):
        self.log_dir = log_dir or os.environ.get('PIPELINE_LOG_DIR') or DEFAULT_LOG_DIR
        self.enabled = _env_flag('PIPELINE_LOG_ENABLED', True)
        self.payloads_enabled = _env_flag('PIPELINE_LOG_PAYLOADS', True)
        self.max_text = _env_int('PIPELINE_LOG_MAX_TEXT', 20000)
        self.max_bytes = _env_int('PIPELINE_LOG_MAX_BYTES', 50 * 1024 * 1024)
        self._lock = threading.Lock()
        self._dirs_ready: set = set()

    # ---- paths ----

    def _ensure_dir(self, path: str) -> None:
        if path in self._dirs_ready:
            return
        os.makedirs(path, exist_ok=True)
        self._dirs_ready.add(path)

    def _run_dir(self, run_id: str) -> str:
        # Keep the run id usable as a directory name.
        safe = re.sub(r'[^A-Za-z0-9._-]', '_', run_id)[:80]
        return os.path.join(self.log_dir, 'runs', safe)

    # ---- writing ----

    def _rotate_if_needed(self, path: str) -> None:
        try:
            if os.path.getsize(path) < self.max_bytes:
                return
        except OSError:
            return
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        base, ext = os.path.splitext(path)
        try:
            os.replace(path, f"{base}.{stamp}{ext}")
        except OSError:
            pass

    def _append(self, path: str, line: str) -> None:
        self._ensure_dir(os.path.dirname(path))
        self._rotate_if_needed(path)
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')

    def _write(self, filename: str, record: Dict[str, Any], run_id: Optional[str]) -> None:
        """Write one JSON line to the global stream and the per-run mirror."""
        line = json.dumps(_redact(_jsonable(record)), ensure_ascii=False)
        with self._lock:
            try:
                self._append(os.path.join(self.log_dir, filename), line)
                if run_id:
                    self._append(os.path.join(self._run_dir(run_id), filename), line)
            except Exception:
                # Logging must never take the pipeline down with it.
                pass

    def _write_text(self, line: str, run_id: Optional[str]) -> None:
        with self._lock:
            try:
                self._append(os.path.join(self.log_dir, 'pipeline.log'), line)
                if run_id:
                    self._append(os.path.join(self._run_dir(run_id), 'pipeline.log'), line)
            except Exception:
                pass

    def _truncate(self, text: str) -> tuple:
        """Return (text, truncated_flag, original_length)."""
        original = len(text)
        if self.max_text > 0 and original > self.max_text:
            return text[:self.max_text], True, original
        return text, False, original

    # ---- public API ----

    def action(
        self,
        component: str,
        action: str,
        *,
        status: str = 'ok',
        target: Optional[str] = None,
        duration_ms: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        action_id: Optional[str] = None,
        debug_id: Optional[str] = None,
    ) -> str:
        """
        Record one action. Never carries model prose - only what happened.

        Returns the action id, so a debug record can point back at it.
        """
        action_id = action_id or _new_id('act')
        if not self.enabled:
            return action_id

        run_id = _run_id.get()
        record = {
            'ts': _now(),
            'action_id': action_id,
            'run_id': run_id,
            'run_kind': _run_kind.get(),
            'stage': _stage.get(),
            'component': component,
            'action': action,
            'target': target,
            'status': status,
            'duration_ms': round(duration_ms, 1) if duration_ms is not None else None,
            'metrics': metrics or {},
            'error': error,
            'debug_id': debug_id,
            'thread': threading.current_thread().name,
        }
        self._write('actions.jsonl', record, run_id)

        parts = [f"[{record['ts']}]"]
        if run_id:
            parts.append(f"run={run_id}")
        if record['stage']:
            parts.append(f"stage={record['stage']}")
        parts.append(f"{component}.{action}")
        if target:
            parts.append(f"target={target}")
        parts.append(f"status={status}")
        if duration_ms is not None:
            parts.append(f"{duration_ms:.0f}ms")
        if metrics:
            parts.append(' '.join(f"{k}={v}" for k, v in metrics.items()))
        if error:
            parts.append(f"error={error}")
        self._write_text(' '.join(str(p) for p in parts), run_id)

        return action_id

    def debug(
        self,
        component: str,
        action: str,
        *,
        action_id: Optional[str] = None,
        target: Optional[str] = None,
        status: str = 'ok',
        inputs: Optional[Dict[str, Any]] = None,
        input_text: Optional[str] = None,
        process: Optional[List[Dict[str, Any]]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        output_text: Optional[str] = None,
        duration_ms: Optional[float] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Record the full detail behind one action, for debugging."""
        debug_id = _new_id('dbg')
        if not (self.enabled and self.payloads_enabled):
            return debug_id

        run_id = _run_id.get()
        record: Dict[str, Any] = {
            'ts': _now(),
            'debug_id': debug_id,
            'action_id': action_id,
            'run_id': run_id,
            'run_kind': _run_kind.get(),
            'stage': _stage.get(),
            'component': component,
            'action': action,
            'target': target,
            'status': status,
            'duration_ms': round(duration_ms, 1) if duration_ms is not None else None,
            'inputs': inputs or {},
            'process': process or [],
            'outputs': outputs or {},
            'error': error,
        }

        if input_text is not None:
            text, truncated, original = self._truncate(str(input_text))
            record['input_text'] = text
            record['input_text_chars'] = original
            record['input_text_truncated'] = truncated

        if output_text is not None:
            text, truncated, original = self._truncate(str(output_text))
            record['output_text'] = text
            record['output_text_chars'] = original
            record['output_text_truncated'] = truncated

        self._write('debug.jsonl', record, run_id)
        return debug_id

    # ---- scopes ----

    @contextmanager
    def run(self, run_id: str, kind: str, **meta: Any) -> Iterator[str]:
        """
        Open a run scope. Every action logged inside carries this run id and is
        mirrored into ``runs/<run_id>/``.
        """
        run_token = _run_id.set(run_id)
        kind_token = _run_kind.set(kind)
        stage_token = _stage.set(None)
        started = time.perf_counter()
        self.action('pipeline', 'run_start', status='start', target=run_id, metrics=meta)
        try:
            yield run_id
        except BaseException as exc:
            self.action(
                'pipeline', 'run_end', status='error', target=run_id,
                duration_ms=(time.perf_counter() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        else:
            self.action(
                'pipeline', 'run_end', status='ok', target=run_id,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        finally:
            _stage.reset(stage_token)
            _run_kind.reset(kind_token)
            _run_id.reset(run_token)

    def begin_stage(self, name: str, **meta: Any) -> tuple:
        """
        Open a stage without a ``with`` block, for long procedural functions
        where the stages are sequential rather than nested. Pair each call with
        ``end_stage`` on the same thread.
        """
        token = _stage.set(name)
        self.action('pipeline', 'stage_start', status='start', target=name, metrics=meta)
        return (token, name, time.perf_counter())

    def end_stage(
        self,
        handle: tuple,
        status: str = 'ok',
        error: Optional[str] = None,
        **metrics: Any,
    ) -> None:
        """Close a stage opened with ``begin_stage``."""
        token, name, started = handle
        self.action(
            'pipeline', 'stage_end', status=status, target=name,
            duration_ms=(time.perf_counter() - started) * 1000,
            metrics=metrics, error=error,
        )
        try:
            _stage.reset(token)
        except ValueError:
            # Reset from a different context; drop back to no stage instead.
            _stage.set(None)

    @contextmanager
    def stage(self, name: str, **meta: Any) -> Iterator[None]:
        """Open a stage scope inside a run."""
        handle = self.begin_stage(name, **meta)
        try:
            yield
        except BaseException as exc:
            self.end_stage(handle, status='error', error=f"{type(exc).__name__}: {exc}")
            raise
        else:
            self.end_stage(handle)

    @contextmanager
    def step(
        self,
        component: str,
        action: str,
        *,
        target: Optional[str] = None,
        **inputs: Any,
    ) -> Iterator['StepRecorder']:
        """
        Time one unit of work and write both an action line and a debug line.

        Any exception is recorded (status=error, with the traceback in the debug
        record) and re-raised unchanged.
        """
        recorder = StepRecorder(self, component, action, target)
        recorder.input(**inputs)
        try:
            yield recorder
        except BaseException as exc:
            recorder.fail(exc)
            raise
        else:
            recorder.finish()

    def llm_call(
        self,
        component: str,
        action: str,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        params: Dict[str, Any],
        response_text: Optional[str],
        duration_ms: float,
        status: str = 'ok',
        target: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
        attempts: int = 1,
        error: Optional[str] = None,
        extra_metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record one model call.

        The action line gets sizes and counts only. The prompt and the reply go
        to the debug stream, which is the point of splitting the two files.
        """
        prompt_chars = sum(len(str(m.get('content') or '')) for m in messages)
        metrics: Dict[str, Any] = {
            'model': model,
            'messages': len(messages),
            'prompt_chars': prompt_chars,
            'response_chars': len(response_text or ''),
            'attempts': attempts,
        }
        if usage:
            metrics.update({k: v for k, v in usage.items() if v is not None})
        if extra_metrics:
            metrics.update(extra_metrics)

        action_id = _new_id('act')
        debug_id = self.debug(
            component, action,
            action_id=action_id,
            target=target,
            status=status,
            inputs={'model': model, 'params': params, 'attempts': attempts},
            input_text=_format_messages(messages),
            outputs={'usage': usage or {}},
            output_text=response_text,
            duration_ms=duration_ms,
            error={'message': error} if error else None,
        )
        self.action(
            component, action,
            status=status,
            target=target,
            duration_ms=duration_ms,
            metrics=metrics,
            error=error,
            action_id=action_id,
            debug_id=debug_id,
        )


def _format_messages(messages: List[Dict[str, Any]]) -> str:
    """Render a chat message list as readable text for the debug stream."""
    blocks = []
    for message in messages:
        role = message.get('role', 'unknown')
        content = message.get('content')
        if not isinstance(content, str):
            content = json.dumps(_jsonable(content), ensure_ascii=False)
        blocks.append(f"----- {role} -----\n{content}")
    return "\n".join(blocks)


class StepRecorder:
    """Collects the detail for one step, then writes the two records."""

    def __init__(
        self,
        logger: PipelineLogger,
        component: str,
        action: str,
        target: Optional[str] = None,
    ):
        self._logger = logger
        self._component = component
        self._action = action
        self._target = target
        self._inputs: Dict[str, Any] = {}
        self._input_text: Optional[str] = None
        self._process: List[Dict[str, Any]] = []
        self._outputs: Dict[str, Any] = {}
        self._output_text: Optional[str] = None
        self._metrics: Dict[str, Any] = {}
        self._started = time.perf_counter()
        self._done = False

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000

    def target(self, value: str) -> 'StepRecorder':
        self._target = value
        return self

    def input(self, **values: Any) -> 'StepRecorder':
        """Structured inputs - parameters, ids, flags. Debug stream only."""
        self._inputs.update(values)
        return self

    def input_text(self, text: Optional[str]) -> 'StepRecorder':
        """The raw text fed into this step. Debug stream only."""
        self._input_text = text
        return self

    def note(self, message: str, **data: Any) -> 'StepRecorder':
        """Add one intermediate processing note to the debug trail."""
        self._process.append({'ts': _now(), 'message': message, **data})
        return self

    def output(self, **values: Any) -> 'StepRecorder':
        """Structured results. Debug stream only."""
        self._outputs.update(values)
        return self

    def output_text(self, text: Optional[str]) -> 'StepRecorder':
        """The raw text this step produced. Debug stream only."""
        self._output_text = text
        return self

    def metric(self, **values: Any) -> 'StepRecorder':
        """Counts and sizes. These *do* appear on the action line."""
        self._metrics.update(values)
        return self

    def _emit(self, status: str, error: Optional[Dict[str, Any]] = None) -> None:
        if self._done:
            return
        self._done = True
        duration = self.elapsed_ms
        action_id = _new_id('act')
        debug_id = self._logger.debug(
            self._component, self._action,
            action_id=action_id,
            target=self._target,
            status=status,
            inputs=self._inputs,
            input_text=self._input_text,
            process=self._process,
            outputs=self._outputs,
            output_text=self._output_text,
            duration_ms=duration,
            error=error,
        )
        self._logger.action(
            self._component, self._action,
            status=status,
            target=self._target,
            duration_ms=duration,
            metrics=self._metrics,
            error=(error or {}).get('message'),
            action_id=action_id,
            debug_id=debug_id,
        )

    def finish(self, status: str = 'ok') -> None:
        self._emit(status)

    def fail(self, exc: BaseException) -> None:
        self._emit('error', {
            'type': type(exc).__name__,
            'message': str(exc),
            'traceback': traceback.format_exc(),
        })


# Shared instance used across the pipeline.
pipeline_log = PipelineLogger()


@contextmanager
def llm_caller(component: str, target: Optional[str] = None) -> Iterator[None]:
    """
    Label the model calls made inside this block.

    LLMClient reads this label so an entry in actions.jsonl says which pipeline
    component made the call instead of a generic 'LLMClient'.
    """
    token = _llm_caller.set((component, target))
    try:
        yield
    finally:
        _llm_caller.reset(token)


_llm_caller: ContextVar[Optional[tuple]] = ContextVar('pipeline_llm_caller', default=None)


def current_llm_caller() -> tuple:
    """Return (component, target) for the enclosing llm_caller block."""
    return _llm_caller.get() or (None, None)
