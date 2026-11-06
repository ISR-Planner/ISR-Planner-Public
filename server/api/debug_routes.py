from fastapi import APIRouter

from server.database.mission_ledger import create_mission_run, log_event

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/api/debug/supabase-test")
def supabase_test():
    run_id = create_mission_run(system_version="debug", mission_name="supabase-test")

    if not run_id:
        return {
            "ok": False,
            "error": "Failed to create mission run (check Railway logs)",
        }

    ok = log_event(run_id, "SUPABASE_TEST_EVENT", {"ok": True})

    return {"ok": ok, "run_id": run_id}
