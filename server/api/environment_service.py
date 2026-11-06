import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple


class EnvironmentService:
    """Stateful environment file/cache helper for the API layer."""

    def __init__(
        self,
        root: Path,
        *,
        get_current_matrix: Callable[[], Optional[Dict[str, Any]]],
        prepare_distance_matrix: Callable[[Dict[str, Any], float], Dict[str, Any]],
        clear_cached_matrix: Callable[[], None],
    ) -> None:
        self.env_path = root / "environment.json"
        self.ai_solution_path = root / "ai_solution.json"
        self.mission_path = root / "mission_solution.json"
        self._get_current_matrix = get_current_matrix
        self._prepare_distance_matrix = prepare_distance_matrix
        self._clear_cached_matrix = clear_cached_matrix
        self._current_env: Optional[Dict[str, Any]] = None
        self._export_counter = 1

    def save_environment(self, env: Dict[str, Any]) -> None:
        self._current_env = env
        self.env_path.write_text(json.dumps(env, indent=2))

    def load_environment(self) -> Optional[Dict[str, Any]]:
        if self._current_env is None and self.env_path.exists():
            self._current_env = json.loads(self.env_path.read_text())
        return self._current_env

    def get_or_create_empty_environment(self) -> Dict[str, Any]:
        env = self.load_environment()
        if env is None:
            env = {
                "airports": [],
                "targets": [],
                "sams": [],
            }
            self._current_env = env
        return env

    def read_environment_text(self) -> Optional[str]:
        if not self.env_path.exists():
            return None
        return self.env_path.read_text()

    def write_heuristic_outputs(
        self,
        *,
        sequences: Dict[str, str],
        drone_configs: Dict[str, Any],
    ) -> None:
        self.ai_solution_path.write_text(json.dumps({"sequences": sequences}, indent=2))
        self.mission_path.write_text(
            json.dumps({"drone_configs": drone_configs}, indent=2)
        )

    def matrix_info(self) -> Tuple[bool, Optional[Dict[str, Any]]]:
        cached_matrix = self._get_current_matrix()
        if cached_matrix is None:
            return False, None
        return True, {
            "num_waypoints": len(cached_matrix.get("labels", [])),
            "num_sam_avoiding_paths": len(cached_matrix.get("paths", {})),
            "excluded_targets": cached_matrix.get("excluded_targets", []),
            "wrapped_polygons": cached_matrix.get("wrapped_polygons", []),
        }

    def environment_response(self) -> Dict[str, Any]:
        env = self.get_or_create_empty_environment()
        matrix_cached, matrix_info = self.matrix_info()
        return {
            "success": True,
            "environment": env,
            "matrix_cached": matrix_cached,
            "matrix_info": matrix_info,
        }

    def refresh_distance_matrix(self, env: Dict[str, Any]) -> None:
        sams = env.get("sams", [])
        if sams:
            print("⏳ Recalculating SAM-aware distance matrix...", flush=True)
            try:
                matrix_data = self._prepare_distance_matrix(env, 0.0)
                num_waypoints = len(matrix_data.get("labels", []))
                num_paths = len(matrix_data.get("paths", {}))
                print(
                    f"✅ Matrix ready: {num_waypoints} waypoints, {num_paths} SAM-avoiding paths",
                    flush=True,
                )
            except Exception as e:
                print(f"⚠️ Matrix calculation failed: {e}", flush=True)
        else:
            self._clear_cached_matrix()
            print("✅ No SAMs - using direct distances", flush=True)

    def build_export(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        env = self.load_environment()
        if env is None:
            return None

        now = datetime.now()
        timestamp = now.strftime("%y%m%d%H")
        filename = f"ise-env-{timestamp}-{self._export_counter}.json"
        self._export_counter += 1

        cached_matrix = self._get_current_matrix()
        export_data: Dict[str, Any] = {
            "environment": env,
            "exported_at": now.isoformat(),
            "matrix_cached": cached_matrix is not None,
        }

        if cached_matrix is not None:
            if "env_hash" in cached_matrix:
                export_data["env_hash"] = cached_matrix["env_hash"]
            if "routing_model" in cached_matrix:
                export_data["routing_model"] = cached_matrix["routing_model"]
            if "routing_model_hash" in cached_matrix:
                export_data["routing_model_hash"] = cached_matrix["routing_model_hash"]
            if "distance_matrix_id" in cached_matrix:
                export_data["distance_matrix_id"] = cached_matrix["distance_matrix_id"]

            export_data["matrix_info"] = {
                "num_waypoints": len(cached_matrix.get("labels", [])),
                "num_sam_avoiding_paths": len(cached_matrix.get("paths", {})),
                "excluded_targets": cached_matrix.get("excluded_targets", []),
                "wrapped_polygons": cached_matrix.get("wrapped_polygons", []),
                "cluster_info": cached_matrix.get("cluster_info", {}),
                "buffer": cached_matrix.get("buffer", 0.0),
                "cache_hit": cached_matrix.get("cache_hit", False),
            }

        return filename, export_data
