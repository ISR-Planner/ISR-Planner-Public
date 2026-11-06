"""
Memory module for ISR Mission Planner.

Provides session state management, constraint programs, and snapshot handling:
- SessionStore: Abstract interface for session persistence
- InMemorySessionStore: Dev/fallback implementation with TTL
- SupabaseSessionStore: Production implementation wrapping session_storage.py
- ConstraintProgram: Schema for user constraint DSL
- ConstraintCompiler: Compiles constraint programs to solver patches
- ConstraintParser: LLM-based parser for natural language constraints
"""

from .constraint_parser import (
    ConstraintParser,
    ParseResult,
    parse_constraints,
)
from .constraints import (
    CompilerResult,
    ConstraintCompiler,
    ConstraintOp,
    ConstraintProgram,
)
from .session_store import (
    ConcurrencyError,
    InMemorySessionStore,
    SessionStore,
    SupabaseSessionStore,
    get_session_store,
)
from .trim import (
    SNAPSHOT_MAX_BYTES,
    approx_size_bytes,
    trim_snapshot,
)

__all__ = [
    # Session store
    "SessionStore",
    "InMemorySessionStore",
    "SupabaseSessionStore",
    "get_session_store",
    "ConcurrencyError",
    # Constraints
    "ConstraintOp",
    "ConstraintProgram",
    "ConstraintCompiler",
    "CompilerResult",
    # Constraint parser
    "ConstraintParser",
    "ParseResult",
    "parse_constraints",
    # Trimming
    "trim_snapshot",
    "approx_size_bytes",
    "SNAPSHOT_MAX_BYTES",
]
