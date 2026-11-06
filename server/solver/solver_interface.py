"""Import and construct the legacy orienteering solver interface."""

import sys
from pathlib import Path
from typing import Any, Optional, Type


def configure_solver_import_paths() -> None:
    """Ensure legacy/local solver import paths are available."""
    legacy_path = Path(__file__).resolve().parents[3] / "legacy" / "isr_legacy_all"
    if str(legacy_path) not in sys.path:
        sys.path.insert(0, str(legacy_path))

    root_path = Path(__file__).resolve().parents[3]
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))

    if "/app" not in sys.path:
        sys.path.insert(0, "/app")


def load_orienteering_solver_interface() -> Optional[Type[Any]]:
    """Load the orienteering interface from Docker or local development paths."""
    configure_solver_import_paths()

    try:
        from webapp.editor.solver.orienteering_interface import (
            OrienteeringSolverInterface as docker_interface_cls,
        )

        print("✅ OrienteeringSolverInterface loaded (Docker path)")
        return docker_interface_cls
    except ImportError as docker_error:
        print(f"⚠️ Docker import failed: {docker_error}")

    try:
        from isr_web.webapp.editor.solver.orienteering_interface import (
            OrienteeringSolverInterface as local_interface_cls,
        )

        print("✅ OrienteeringSolverInterface loaded (local path)")
        return local_interface_cls
    except ImportError as local_error:
        print(f"⚠️ Local import failed: {local_error}")
        print(
            "❌ OrienteeringSolverInterface not available - orienteering features disabled"
        )
        return None


OrienteeringSolverInterface = load_orienteering_solver_interface()


def create_solver_interface(
    interface_cls: Optional[Type[Any]] = OrienteeringSolverInterface,
) -> Optional[Any]:
    """Create the global solver instance used by compatibility entrypoints."""
    if interface_cls is None:
        print(
            "⚠️ OrienteeringSolverInterface not available - solver bridge will have limited functionality",
            flush=True,
        )
        return None

    print("📦 Initializing OrienteeringSolverInterface...", flush=True)
    solver = interface_cls()
    print("📦 Solver interface initialized", flush=True)
    return solver
