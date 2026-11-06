# docs/agents — Agent System Documentation

## Current Agents tab

The UI's **Agents → Send** action calls `/api/agents/reason`, implemented by `server/api/agent_routes.py` and `server/agents/reasoning_agent.py`. The saved **AI Settings** provider/model is passed with each request. Provider credentials come only from the server environment; client-supplied API keys are ignored. Without a saved selection, the agent uses the server's `LLM_PROVIDER` / `LLM_MODEL` defaults (OpenAI / GPT-4o when unset).

For current demos, use the [Railway weather preview](https://isr-planner-production.up.railway.app/static/weather_preview.html). Deployment `26b394f` was verified successful on October 13, 2026. Local Agent credential repair is deferred. **Run Planner** uses the mission solver and does not require an LLM key.

1. Before every fresh import or **Load demo**, hard refresh: **Cmd+Shift+R** on Mac or **Ctrl+Shift+R** on Windows/Linux. This applies to both Railway and localhost; it reloads current scripts and starts a fresh page before import initializes the mission.
2. Import the mission or load the demo.
3. In the gear / **AI Settings**, choose **OpenAI → GPT-4o**, click **Test Connection**, and then **Save Settings** after success.
4. Send the instruction in **Agents**. Review and **Accept** the draft before **Animate**.

For later local repair, replace the credential privately in the project `.env`, then restart with `./run_planner.sh`. This local launcher explicitly loads the project's `.env` over stale inherited terminal values. Railway continues to use its deployment environment. A "configured" provider indicates a key is present; **Test Connection** verifies authentication.

## Reference and historical documentation

| File | Description |
|------|-------------|
| [AGENTIC_SYSTEM_DESCRIPTION.md](AGENTIC_SYSTEM_DESCRIPTION.md) | v4 multi-agent architecture, prompts, and tool contracts; not the current Agents tab entry point |
| [AGENT_CONTINUE_FROM_CUT.md](AGENT_CONTINUE_FROM_CUT.md) | Agent-driven segmented missions: continue/replan from a mid-flight cut |
| [AGENTIC_EXTENSION_PROPOSAL.md](AGENTIC_EXTENSION_PROPOSAL.md) | Historical v3-era proposal for extending the agent system |
| [MISSION_ORCHESTRATION_TOOLS.md](MISSION_ORCHESTRATION_TOOLS.md) | API reference for agent orchestration tools |
| [V4_IMPLEMENTATION_COMPLETE.md](V4_IMPLEMENTATION_COMPLETE.md) | v4 agent implementation completion notes |
| [V3_TO_V4_MIGRATION.md](V3_TO_V4_MIGRATION.md) | Historical migration guide from v3 task-based agents to v4 LangGraph |
| [V3_VS_V4_COMPARISON.md](V3_VS_V4_COMPARISON.md) | Historical side-by-side comparison from before v4 became active |

`server/agents/isr_agent_multi_v4.py` remains available through v4 endpoints and comparison workflows. The architecture and migration documents above retain their version-specific context.
Legacy v1–v3 agent files are in `server/agents/archive/` and `server/agents/versions/`.
