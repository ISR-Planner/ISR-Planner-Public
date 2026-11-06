# server/agents/archive — Legacy Agent Versions

These files have been archived as part of the repository cleanup. They are **not used** in the production agent system and are preserved for historical reference only.

| File | Was | Reason archived |
|------|-----|-----------------|
| `isr_agent.py` | v3 agent shim used by the legacy test harness | Only imported by `tests/archive/test_multi_agent_legacy.py`; no production usage |
| `isr_agent_multi_v2.py` | v2 multi-agent system | Only imported by `isr_agent_multi_v3.py` (also archived); superseded by v4 |
| `isr_agent_multi_v3.py` | v3 multi-agent system (task-based) | Only imported by `isr_agent.py` (also archived); superseded by v4 |
| `reasoning_agent.py` | Experimental reasoning agent | Zero active imports; standalone prototype |

The active agent is `server/agents/isr_agent_multi_v4.py`.

> Note: `server/agents/versions/` (containing the v1 single agent `isr_agent_single_v1.py`) is a separate, intentionally preserved directory and is **not** part of this archive.
