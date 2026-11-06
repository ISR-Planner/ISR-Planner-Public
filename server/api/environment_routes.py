import os
from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from .environment_service import EnvironmentService
from .run_ledger_service import RunLedgerService


def create_environment_router(
    environment_service: EnvironmentService,
    run_ledger: RunLedgerService,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/environment")
    def receive_environment(payload: Dict[str, Any]):
        """
        Payload: { "environment": { airports: [...], targets: [...], sams: [...] } }

        Automatically recalculates SAM-aware distance matrix after each edit.
        Also snapshots the env into Supabase (env_versions) and logs events.
        """
        print("✅ HIT POST /api/environment", flush=True)
        print("✅ SUPABASE_URL set:", bool(os.environ.get("SUPABASE_URL")), flush=True)
        print("✅ SUPABASE_KEY set:", bool(os.environ.get("SUPABASE_KEY")), flush=True)
        print("✅ payload keys:", list(payload.keys()), flush=True)

        env = payload.get("environment")
        if not isinstance(env, dict):
            return {"success": False, "error": "Invalid payload"}

        # ---- Update in-memory + on-disk env (existing behavior) ----
        environment_service.save_environment(env)
        print("📥 [ISR_WEB] Environment updated", flush=True)

        ledger_snapshot = run_ledger.snapshot_environment_update(env)

        # ---- Existing behavior: matrix recompute ----
        environment_service.refresh_distance_matrix(env)

        return {
            "success": True,
            "run_id": ledger_snapshot["run_id"],
            "env_version_id": ledger_snapshot["env_version_id"],
            "event_type": ledger_snapshot["event_type"],
        }

    @router.get("/api/environment")
    def get_environment():
        return environment_service.environment_response()

    @router.get("/api/export_environment")
    def export_environment():
        """
        Export environment with SAM-aware distance matrix reference in JSON format.

        The exported JSON keeps the environment clean and includes only references
        to the distance matrix (stored in Supabase), not the matrix itself.
        """
        export = environment_service.build_export()
        if export is None:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": "No environment loaded"},
            )
        filename, export_data = export

        print(f"📤 [ISR_WEB] Exporting environment as {filename}", flush=True)
        if "distance_matrix_id" in export_data:
            print(
                f"   📎 Matrix ref: {export_data['distance_matrix_id'][:8]}...",
                flush=True,
            )

        # Return JSON response with Content-Disposition header to force filename
        return JSONResponse(
            content=export_data,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "application/json",
            },
        )

    @router.get("/debug/env", response_class=PlainTextResponse)
    def debug_env():
        env_text = environment_service.read_environment_text()
        if env_text is None:
            return "No environment.json yet."
        return env_text

    return router
