"""
WSGI entry point for running under gunicorn.

run.py is the development entry point: it owns the dev server, the port and
the reloader. A WSGI server imports the app instead of calling app.run(), so
the configuration check that run.py performs in main() would otherwise be
skipped and the process would start with missing credentials and only fail on
the first request.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402

_errors = Config.validate()
if _errors:
    for _err in _errors:
        print(f"Configuration error: {_err}", file=sys.stderr)
    raise SystemExit(1)

application = create_app()
app = application
