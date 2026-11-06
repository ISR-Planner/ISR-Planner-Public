"""ISR Editor - Modular Implementation

`ISREditor` is a Tk desktop application, but this package also carries the
headless orienteering solver used by the server and the benchmark harness
(`webapp.editor.solver.orienteering_interface`). Importing the GUI eagerly made
that solver unimportable on any machine without `tkinter`: the solver loader in
`server/solver/solver_interface.py` swallows the ImportError and prints
"orienteering features disabled", so missions silently degraded to a fallback
decoder instead of failing loudly.

The GUI is therefore resolved lazily. Desktop callers still get `ISREditor` via
normal attribute access; headless callers never touch tkinter.
"""

from typing import Any

__all__ = ["ISREditor"]


def __getattr__(name: str) -> Any:
    if name == "ISREditor":
        from .main import ISREditor

        return ISREditor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
