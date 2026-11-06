"""Direct offline backend calls for the checkpoint regression; no HTTP access."""

import contextlib
import io
import json
import sys

request = json.loads(sys.stdin.readline())
with contextlib.redirect_stdout(io.StringIO()):
    from server.api.models import SolveWithAllocationRequest
    from server.api.solve_routes import run_solve_with_allocation
    from server.solver.weather_impact import assess_impact

    if request["operation"] == "impact":
        result = assess_impact(**request["arguments"])
    else:
        solved = run_solve_with_allocation(
            SolveWithAllocationRequest(**request["arguments"])
        )
        result = solved.model_dump() if hasattr(solved, "model_dump") else solved.dict()
print(json.dumps(result))
