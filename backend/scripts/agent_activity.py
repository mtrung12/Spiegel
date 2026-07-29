"""
Agent activity-window helpers shared by the platform simulation scripts.

`active_hours` reaches the runner straight out of an LLM-written config, so its
shape is not guaranteed. The runner compares it against an int hour, and an
unnormalised `["18:00", "23:00"]` silently matches nothing - every agent is
filtered out and the run produces only its seeded posts.
"""

from typing import Any, List

# Full-day fallback split: agents default to waking hours rather than to
# "always on", which would overstate activity for every misconfigured agent.
DEFAULT_ACTIVE_HOURS = list(range(8, 23))


def _coerce_hour(value: Any) -> int:
    """Read one hour-of-day out of an int, a float, "18" or "18:00"."""
    if isinstance(value, bool):
        raise ValueError("bool is not an hour")
    if isinstance(value, (int, float)):
        hour = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty hour")
        hour = int(text.split(':', 1)[0])
    else:
        raise ValueError(f"unsupported hour type: {type(value).__name__}")

    if not 0 <= hour <= 23:
        raise ValueError(f"hour out of range: {hour}")
    return hour


def normalize_active_hours(value: Any) -> List[int]:
    """
    Return the hours-of-day an agent is active, as ints in 0-23.

    Accepts the shapes an LLM actually emits for this field:
      - [9, 10, 11]            an explicit hour list
      - ["18:00", "23:00"]     a two-element window, read as [start, end)
      - "18:00-23:00"          the same window as one string
    A two-element list is treated as a window rather than as two isolated
    hours: that is what the model means by it, and reading it literally leaves
    an agent awake for two hours a day.
    Anything unparseable falls back to DEFAULT_ACTIVE_HOURS.
    """
    if value is None:
        return list(DEFAULT_ACTIVE_HOURS)

    if isinstance(value, str):
        parts = [p for p in value.replace('~', '-').split('-') if p.strip()]
        value = parts if len(parts) == 2 else [value]

    if not isinstance(value, (list, tuple)):
        value = [value]

    hours: List[int] = []
    for item in value:
        try:
            hours.append(_coerce_hour(item))
        except (ValueError, TypeError):
            continue

    if not hours:
        return list(DEFAULT_ACTIVE_HOURS)

    # A two-element list is a window. Ranges may wrap past midnight
    # (22:00-02:00), so walk forward from the start rather than sorting.
    if len(hours) == 2 and hours[0] != hours[1]:
        start, end = hours
        span = (end - start) % 24
        return [(start + offset) % 24 for offset in range(span)]

    return sorted(set(hours))
