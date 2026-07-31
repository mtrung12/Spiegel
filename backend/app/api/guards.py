"""
Request-level identifier validation.

Resource ids reach `os.path.join` in the managers and, in the download
handlers, `send_file`. Validating inside each handler would mean auditing ~37
routes and every future one, so this installs a single `before_request` hook per
blueprint instead: a malformed id is rejected before any handler body runs.

Ids arrive from three places and all three reach the same path joins, so all
three are checked:

  * URL path segments  (``/api/simulation/<simulation_id>``)
  * JSON request bodies (``{"simulation_id": ...}``)
  * query strings      (``?platform=twitter``)

Keys are matched by name against the table below, so a handler that reads a new
id under one of these names is covered without touching this file.
"""

from flask import jsonify, request

from ..utils.identifiers import (
    InvalidIdentifierError,
    validate_id,
    validate_platform,
)
from ..utils.locale import t
from ..utils.logger import get_logger

logger = get_logger('spiegel.api.guards')

# Parameter name -> identifier kind understood by validate_id. Names not listed
# here are free-form and validated by the handler that consumes them
# (`entity_type` is a graph node label, `script_name` has its own allowlist).
_ID_PARAM_KINDS = {
    'simulation_id': 'simulation_id',
    'project_id': 'project_id',
    'report_id': 'report_id',
    'graph_id': 'graph_id',
    'task_id': 'task_id',
    'entity_uuid': 'entity_uuid',
}


def _check(name: str, value) -> None:
    """Validate one name/value pair, ignoring anything not an id."""
    # Absent values are the handler's business: a route that requires an id
    # reports its own "missing" error, and an optional one has a default.
    if value is None:
        return

    if name == 'platform':
        validate_platform(value)
        return

    kind = _ID_PARAM_KINDS.get(name)
    if kind is not None:
        validate_id(kind, value)


def _validate_request() -> None:
    """
    Reject malformed identifiers anywhere in the request.

    Returns a 404 rather than a 400: the tests pin this, and it is also the
    better answer - a traversal probe learns nothing about what exists.
    """
    try:
        for name, value in (request.view_args or {}).items():
            _check(name, value)

        # silent=True: a malformed body is not this hook's error to report. Guard
        # on is_json so a GET with no body does not attempt a parse.
        if request.is_json:
            body = request.get_json(silent=True)
            if isinstance(body, dict):
                for name, value in body.items():
                    _check(name, value)

        for name, value in request.args.items():
            _check(name, value)

    except InvalidIdentifierError as exc:
        # Log the rejected value - it is the useful half of a traversal probe -
        # but keep it out of the response body.
        logger.warning(
            "rejected %s in %s %s: %r",
            exc.kind, request.method, request.path, exc.value,
        )
        # Flask short-circuits the request when a before_request hook returns a
        # value, so the handler never runs.
        return jsonify({
            "success": False,
            "error": t('api.resourceNotFound'),
        }), 404

    return None


def register_identifier_guard(*blueprints) -> None:
    """Install the validation hook on each blueprint."""
    for blueprint in blueprints:
        blueprint.before_request(_validate_request)
