"""
Shared pytest configuration.

Set before any test module imports the app, so the pipeline logger picks the
disabled flag up when its singleton is constructed. Test runs would otherwise
write fixture traffic into local-doc/logs, which is meant to hold real runs.
"""

import os

os.environ.setdefault('PIPELINE_LOG_ENABLED', 'false')
