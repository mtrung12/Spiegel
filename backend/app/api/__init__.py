"""
API routes.
"""

from flask import Blueprint

graph_bp = Blueprint('graph', __name__)
simulation_bp = Blueprint('simulation', __name__)
report_bp = Blueprint('report', __name__)

from . import graph  # noqa: E402, F401
from . import simulation  # noqa: E402, F401
from . import report  # noqa: E402, F401

# Reject malformed resource ids before any handler runs. Registered here rather
# than in each module so a new route is covered by default; see guards.py.
from .guards import register_identifier_guard  # noqa: E402

register_identifier_guard(graph_bp, simulation_bp, report_bp)

