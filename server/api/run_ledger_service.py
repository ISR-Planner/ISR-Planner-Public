from dataclasses import dataclass
from typing import Any, Dict, Optional

from server.database.mission_ledger import (
    create_env_version,
    create_mission_run,
    create_plan,
    log_event,
)


@dataclass
class RunLedgerService:
    """Small stateful facade for legacy Supabase mission ledger calls."""

    current_run_id: Optional[str] = None
    current_env_version_id: Optional[str] = None
    current_plan_id: Optional[str] = None

    def ensure_session_run(
        self,
        *,
        system_version: str = "v4",
        mission_name: str = "isr-run",
        created_source: Optional[str] = None,
    ) -> Optional[str]:
        if self.current_run_id is None:
            self.current_run_id = create_mission_run(
                system_version=system_version,
                mission_name=mission_name,
            )
            if self.current_run_id and created_source:
                log_event(
                    self.current_run_id,
                    "RUN_CREATED",
                    {"source": created_source},
                )
        return self.current_run_id

    def snapshot_environment_update(self, env: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_session_run(created_source="api/environment")

        event_type = (
            "ENV_IMPORTED" if self.current_env_version_id is None else "ENV_EDITED"
        )

        if self.current_run_id is not None:
            self.current_env_version_id = create_env_version(
                run_id=self.current_run_id,
                env_snapshot=env,
                source="human",
                reason=event_type.lower(),  # "env_imported" or "env_edited"
            )

            print(
                f"✅ create_env_version returned: {self.current_env_version_id}",
                flush=True,
            )

            try:
                airports_n = len(env.get("airports", []))
                targets_n = len(env.get("targets", []))
                sams_n = len(env.get("sams", []))
            except Exception:
                airports_n = targets_n = sams_n = None  # type: ignore[assignment]

            log_event(
                self.current_run_id,
                event_type,
                {
                    "env_version_id": self.current_env_version_id,
                    "counts": {
                        "airports": airports_n,
                        "targets": targets_n,
                        "sams": sams_n,
                    },
                    "has_sams": bool(env.get("sams", [])),
                },
            )

        return {
            "run_id": self.current_run_id,
            "env_version_id": self.current_env_version_id,
            "event_type": event_type,
        }

    def snapshot_solve_environment(self, env: Dict[str, Any]) -> Optional[str]:
        self.ensure_session_run()

        if self.current_run_id is None:
            return None

        print(
            "✅ About to create_env_version. run_id =", self.current_run_id, flush=True
        )
        try:
            self.current_env_version_id = create_env_version(
                run_id=self.current_run_id,
                env_snapshot=env,
                source="human",
                reason="solve",
            )
            print(
                "✅ create_env_version returned:",
                self.current_env_version_id,
                flush=True,
            )
        except Exception as e:
            print("❌ create_env_version raised:", repr(e), flush=True)
            raise

        return self.current_env_version_id

    def create_draft_plan(
        self,
        *,
        trajectories: Dict[str, Any],
        metrics: Dict[str, Any],
        notes: str,
    ) -> Optional[str]:
        if self.current_run_id is None:
            return None

        print(
            "✅ About to create_plan. env_version_id =",
            self.current_env_version_id,
            flush=True,
        )
        try:
            self.current_plan_id = create_plan(
                run_id=self.current_run_id,
                env_version_id=self.current_env_version_id,
                status="draft",
                starts_by_drone={},
                allocation={},
                trajectories=trajectories,
                metrics=metrics,
                notes=notes,
            )
            print("✅ create_plan returned:", self.current_plan_id, flush=True)
        except Exception as e:
            print("❌ create_plan raised:", repr(e), flush=True)
            raise

        return self.current_plan_id

    def create_solver_run(
        self,
        *,
        allocation_strategy: str,
        is_checkpoint_replan: bool,
    ) -> Optional[str]:
        return create_mission_run(
            system_version="solve_with_allocation",
            mission_name="planner-solve",
            objective_weights={"allocation_strategy": allocation_strategy},
            constraints={"is_checkpoint_replan": is_checkpoint_replan},
        )

    def log_event(
        self,
        run_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> bool:
        return log_event(run_id, event_type, payload)
