"""
Identifier validation.

Every resource id in this project is generated server-side from a uuid4 hex
slice (see ProjectManager.create_project, SimulationManager.create_simulation,
ReportManager and GraphBuilderService). That makes the accepted shape narrow
and known ahead of time, so ids arriving from a URL can be checked against the
generator rather than merely sanitized.

This matters because ids are joined onto filesystem paths. `os.path.join` with
an unvalidated `../..` segment escapes the data directory, and several handlers
pass the result to send_file. Validating on the way in is the single choke
point that closes that off, rather than auditing each join site.
"""

import re
from typing import Optional

# Mirrors the generators: f"{prefix}_{uuid.uuid4().hex[:n]}"
_ID_PATTERNS = {
    'project_id': re.compile(r'^proj_[0-9a-f]{12}$'),
    'simulation_id': re.compile(r'^sim_[0-9a-f]{12}$'),
    'report_id': re.compile(r'^report_[0-9a-f]{12}$'),
    # Zep graph ids default to spiegel_<16>, but a graph may also be created
    # with a caller-supplied id, so this stays a general safe-token rule.
    'graph_id': re.compile(r'^[A-Za-z0-9_-]{1,128}$'),
    # Zep entity uuids are standard uuid4 strings.
    'entity_uuid': re.compile(r'^[0-9a-fA-F-]{1,64}$'),
    'task_id': re.compile(r'^[0-9a-fA-F-]{1,64}$'),
}

# Path segments are never legal in an id, whatever the pattern allows.
_TRAVERSAL = re.compile(r'[/\\]|\.\.')


class InvalidIdentifierError(ValueError):
    """Raised when a caller-supplied identifier fails validation."""

    def __init__(self, kind: str, value: str):
        self.kind = kind
        self.value = value
        super().__init__(f"Invalid {kind}")


def validate_id(kind: str, value: Optional[str]) -> str:
    """
    Check an identifier against the shape its generator produces.

    Args:
        kind: One of the keys in _ID_PATTERNS
        value: The candidate identifier

    Returns:
        The identifier unchanged, when valid

    Raises:
        InvalidIdentifierError: The value is missing or malformed
        KeyError: `kind` is not a known identifier kind (a programming error)
    """
    pattern = _ID_PATTERNS[kind]

    if not value or not isinstance(value, str):
        raise InvalidIdentifierError(kind, str(value))
    if _TRAVERSAL.search(value):
        raise InvalidIdentifierError(kind, value)
    if not pattern.match(value):
        raise InvalidIdentifierError(kind, value)

    return value


def is_valid_id(kind: str, value: Optional[str]) -> bool:
    """Non-raising form of validate_id, for call sites that branch instead."""
    try:
        validate_id(kind, value)
        return True
    except InvalidIdentifierError:
        return False


# Platform is not an id, but it is interpolated into a database filename
# ("{platform}_simulation.db"), so it needs the same treatment.
VALID_PLATFORMS = frozenset({'twitter', 'reddit'})


def validate_platform(value: Optional[str]) -> str:
    """Check a platform name that will be interpolated into a filename."""
    if value not in VALID_PLATFORMS:
        raise InvalidIdentifierError('platform', str(value))
    return value
