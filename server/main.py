import sys
from pathlib import Path

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    HTMLResponse,
)
from fastapi.staticfiles import StaticFiles

from .api.agent_routes import router as agent_router
from .api.catalog_routes import router as catalog_router
from .api.client_config_routes import router as client_config_router
from .api.debug_routes import router as debug_router
from .api.environment_routes import create_environment_router
from .api.environment_service import EnvironmentService
from .api.executive_routes import router as executive_router
from .api.gnn_routes import router as gnn_router
from .api.legacy_agent_routes import create_legacy_agent_router
from .api.llm_routes import router as llm_router
from .api.matrix_routes import router as matrix_router
from .api.mission_routes import router as mission_router
from .api.optimizer_routes import router as optimizer_router
from .api.run_ledger_service import RunLedgerService
from .api.solve_routes import create_solve_router
from .api.weather_routes import router as weather_router
from .solver.solver_bridge import (
    clear_cached_matrix,
    get_current_matrix,
    prepare_distance_matrix,
)

run_ledger = RunLedgerService()

# Import polygon wrapping for visualization (same as delivery planner)
# Add paths for both local dev and Docker deployment
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

# Support both local development and Docker deployment paths
if Path("/app/webapp").exists():
    # Docker deployment
    ROOT = Path("/app")
    WEBAPP = ROOT / "webapp"
else:
    # Local development
    ROOT = Path(__file__).resolve().parents[1]  # isr_web/
    WEBAPP = ROOT / "webapp"

environment_service = EnvironmentService(
    ROOT,
    get_current_matrix=get_current_matrix,
    prepare_distance_matrix=prepare_distance_matrix,
    clear_cached_matrix=clear_cached_matrix,
)

app = FastAPI()
app.include_router(client_config_router)
app.include_router(weather_router)
app.include_router(catalog_router)
app.include_router(mission_router)
app.include_router(llm_router)
app.include_router(create_environment_router(environment_service, run_ledger))
app.include_router(matrix_router)
app.include_router(create_solve_router(run_ledger))
app.include_router(agent_router)
app.include_router(optimizer_router)
app.include_router(debug_router)
app.include_router(executive_router)
app.include_router(gnn_router)
app.include_router(create_legacy_agent_router(environment_service))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Middleware to add no-cache headers for JavaScript files
@app.middleware("http")
async def add_no_cache_for_js(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.endswith(".js"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# serve static files (index.html, isr.js, etc.)
# NOTE: Middleware above adds no-cache headers for all .js files
app.mount("/static", StaticFiles(directory=str(WEBAPP)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main ISR Web UI page."""
    index_path = WEBAPP / "index.html"
    if not index_path.exists():
        # Helpful message if the file isn't there yet
        return HTMLResponse(
            "<h1>ISR Web UI</h1><p>index.html not found in /webapp</p>",
            status_code=200,
        )

    return HTMLResponse(index_path.read_text(encoding="utf-8"))
