"""
Token accounting for the OASIS simulation subprocess.

The backend meters its own LLM calls in `app/utils/openai_chat_compat`, but the
simulation runs in a separate process against a camel model built by
`ModelFactory`, so agent steps and interviews never reach that meter. On a run
with many agents and rounds those calls are the bulk of the spend, and they
were invisible.

`instrument_model` wraps a camel model in place and records the usage every
completion reports. Totals are written next to the run's other artefacts as
`token_usage.json`.
"""

import json
import os
import threading
from typing import Any, Dict, Optional


class TokenMeter:
    """Process-wide token totals, split per label (platform, interview, ...)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals: Dict[str, Dict[str, int]] = {}

    def record(self, label: str, prompt: int, completion: int) -> None:
        with self._lock:
            bucket = self._totals.setdefault(
                label, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
            )
            bucket["calls"] += 1
            bucket["prompt_tokens"] += prompt
            bucket["completion_tokens"] += completion

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            by_label = {k: dict(v) for k, v in self._totals.items()}

        total = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        for bucket in by_label.values():
            for key in total:
                total[key] += bucket[key]
        total["total_tokens"] = total["prompt_tokens"] + total["completion_tokens"]
        return {"total": total, "by_label": by_label}

    def write(self, simulation_dir: str) -> Optional[str]:
        """Persist the totals. Never raises: accounting must not fail a run."""
        try:
            path = os.path.join(simulation_dir, "token_usage.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.snapshot(), f, ensure_ascii=False, indent=2)
            return path
        except Exception:
            return None


METER = TokenMeter()


def _extract_usage(response: Any) -> Optional[Dict[str, int]]:
    """Read prompt/completion counts off a completion, when the provider sends them."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return None

    def field(name: str) -> int:
        if isinstance(usage, dict):
            return int(usage.get(name) or 0)
        return int(getattr(usage, name, 0) or 0)

    return {"prompt": field("prompt_tokens"), "completion": field("completion_tokens")}


def instrument_model(model: Any, label: str, logger: Any = None) -> Any:
    """
    Wrap a camel model's run methods so every completion is counted.

    The model object is patched in place and returned, so callers can drop this
    in wherever ModelFactory.create was used. A streamed response carries no
    usage block and is simply not counted - under-reporting is preferable to
    guessing.
    """
    for method_name in ("run", "arun"):
        original = getattr(model, method_name, None)
        if original is None or getattr(original, "_token_metered", False):
            continue

        if method_name == "arun":
            async def wrapped(*args, _original=original, **kwargs):
                response = await _original(*args, **kwargs)
                _count(response, label, logger)
                return response
        else:
            def wrapped(*args, _original=original, **kwargs):
                response = _original(*args, **kwargs)
                _count(response, label, logger)
                return response

        wrapped._token_metered = True
        try:
            setattr(model, method_name, wrapped)
        except Exception:
            # Some model classes expose read-only attributes; skip rather than
            # take the run down over accounting.
            continue

    return model


def _count(response: Any, label: str, logger: Any = None) -> None:
    try:
        usage = _extract_usage(response)
        if not usage:
            return
        METER.record(label, usage["prompt"], usage["completion"])
        if logger is not None:
            snapshot = METER.snapshot()["total"]
            logger.info(
                f"[tokens] {label} prompt={usage['prompt']} "
                f"completion={usage['completion']} "
                f"(run total prompt={snapshot['prompt_tokens']}, "
                f"completion={snapshot['completion_tokens']})"
            )
    except Exception:
        pass
