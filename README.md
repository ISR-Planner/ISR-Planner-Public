# ISR Mission Planner

A web application for planning multi-drone Intelligence, Surveillance, and Reconnaissance (ISR) missions. Build mission environments, configure up to five drones, calculate routes around threats, and review mission playback. Optional AI agents accept natural-language instructions.

**Run Planner does not require an LLM API key.** Hosted AI providers require a valid server-side credential for the **Agents** workflow.

## Choose how to run

- **Local application:** Follow the Windows or macOS/Linux instructions below. Python and dependencies run on your machine.
- **Hosted demo:** Open the [Railway application](https://isr-planner-production.up.railway.app/) or its [weather preview](https://isr-planner-production.up.railway.app/static/weather_preview.html). That deployment comes from a separate repository; editing this checkout does not change it. Railway runs the backend remotely, and your browser accesses it.

## Prerequisites

- **Python 3.11 or newer.** Python 3.11 is an option, not a requirement to use that exact version. CrewAI is optional and has its own Python compatibility limits; see [Optional CrewAI](#optional-crewai-comparison-tab).
- This repository, cloned or downloaded and extracted. Open its root folder in VS Code: the folder containing `requirements.txt` and `server/`.
- Permission to install packages from your approved Python package source. On managed machines, ask your administrator if installation or executable access is blocked.
- Network access for installation and external services such as maps, weather feeds, hosted AI providers, and Supabase when configured.

Docker and Node.js are not required to run the Python application. Node.js 22+ is used for frontend development checks.

## Windows setup (PowerShell)

Run these commands in VS Code's **Terminal → New Terminal**, using PowerShell. Replace the example path if your project is elsewhere.

### 1. Create a Python environment

```powershell
Set-Location C:\ISR-Planner-Production
py --version
py -m venv .venv
```

Check that the reported Python version is at least 3.11. If `py` is unavailable, use `python --version` and `python -m venv .venv`, or the full path to your installed Python executable. To select a particular installed version, use a version selector such as `py -3.13 -m venv .venv`.

If switching Python versions, create a fresh environment. If `.venv` already exists, use a different environment name and substitute it in the commands below. Do not reuse an environment copied from another machine or OS.

### 2. Install dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

These commands use the environment's Python directly; activating it is optional. In VS Code, press **Ctrl+Shift+P**, run **Python: Select Interpreter**, and choose `.venv\Scripts\python.exe`.

### 3. Start the server

From the project root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8893
```

Wait for **Application startup complete**, then open **http://127.0.0.1:8893**. Keep the terminal open; press **Ctrl+C** to stop the server.

For later sessions, open the project root and run the same server command. No dependency reinstall is needed unless the requirements change.

`run_planner.sh` is a Bash launcher for macOS/Linux. PowerShell and Python cannot execute it as a native script; use the Uvicorn command above on Windows.

## macOS / Linux setup

From the repository root, with Python 3.11 or newer installed:

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip check
python3 -m uvicorn server.main:app --host 127.0.0.1 --port 8893
```

Wait for **Application startup complete**, then open **http://127.0.0.1:8893**. Keep the terminal open; press **Ctrl+C** to stop the server.

For later sessions, activate `.venv` and run the Uvicorn command again. For development, append `--reload` to restart the server when Python files change.

The existing `./run_planner.sh` launcher is an alternative on macOS/Linux with Bash and `lsof` available. It activates `.venv` or `venv`, stops any process listening on port 8893, loads the project `.env` over inherited environment values, and starts Uvicorn with reload enabled.

## Optional CrewAI (Comparison tab)

CrewAI is used only for its entrant in the **Comparison** tab (labeled **Compare** in the UI). It is not needed for **Run Planner** or the **Agents** tab, and its requirement is commented out by default.

To enable CrewAI:

1. Use a compatible Python version. **Python 3.13 works; Python 3.11 is another option.** CrewAI currently requires Python below 3.14; check the [CrewAI package requirements](https://pypi.org/project/crewai/) when choosing a release. If changing Python versions, create a fresh environment using that interpreter.
2. Uncomment `# crewai>=0.100.0` in `requirements.txt`.
3. Rerun the dependency installation and `pip check` commands for your OS, then restart the server.

Leave CrewAI commented out if you do not need it. Comparisons that include the CrewAI entrant require it to be installed; the remaining application workflows do not. The Railway Dockerfile and CI currently use Python 3.11 independently of your local Python choice.

## Optional configuration

Basic **Run Planner** usage needs no `.env` file. For hosted AI, create `.env` in the project root with the provider settings you need, for example:

```dotenv
OPENAI_API_KEY=replace_with_your_actual_key
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
```

See [`.env.example`](.env.example) for other providers and optional integrations. If copying that file, remove or leave blank unused placeholder credentials and omit its Docker-specific `PYTHONPATH=/app` setting for local use. Supabase-backed features require the corresponding database configuration; the basic planner can run without it.

The application loads `.env` at startup. With the direct Uvicorn commands above, existing process environment variables take precedence. Restart after changing configuration. Keep API keys in the server environment or `.env`; do not commit them.

To use an agent:

1. Load a mission or demo.
2. Open **AI Settings**, select the configured provider and model, then click **Test Connection**.
3. After a successful connection, click **Save Settings**.
4. Open **Agents**, enter an instruction, and click **Send**. Review and **Accept** the draft before **Animate**.

Saved browser settings select the provider/model; they do not supply API keys. A provider marked "configured" means a credential is present, not that authentication has succeeded. See the [agent documentation](docs/agents/README.md) for request-path details and `.env.example` for local Ollama options without an API key.

## First mission

1. **Load an environment:** Import mission JSON or choose **Load demo**. Before a fresh import, hard refresh with **Ctrl+Shift+R** on Windows/Linux or **Cmd+Shift+R** on macOS to avoid stale scripts and mission state.
2. **Edit the map:** Enable **Edit** to add or move targets, airports, and SAM threats. Alternatively, use the imported environment as-is.
3. **Configure drones:** Enable the drones you need and check their airports, fuel budgets, and permitted target types.
4. **Plan:** Click **Run Planner** in Mission Control. Review routes, target coverage, fuel usage, and any warnings.
5. **Review and play:** Accept a draft when prompted, then use **Animate** to review the mission.
6. **Save:** Export your environment and solution as JSON. Do not rely on an open server session as your only copy; some state is stored in memory and resets on restart.

For larger missions, use segments and checkpoints to plan or replan individual legs. Solver details are in [docs/solver/](docs/solver/).

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `pip.exe` reports "Access is denied" | Use the environment's `python.exe -m pip` command above. If Python execution is also denied, send the executable path and error to your administrator. |
| `No matching distribution found for crewai` with Python 3.14 | Leave CrewAI commented out for normal use. To include it in Comparison, install it in a fresh environment using a compatible Python version, such as 3.13 or 3.11. |
| `run_planner.sh` does nothing, or Python reports a syntax error in it | On Windows, use the PowerShell Uvicorn command above. |
| `No module named uvicorn` or another missing package | Install requirements using the same environment's Python that starts the server. Check the final installation error; "Collecting" messages alone do not mean installation succeeded. |
| `Could not import module server.main` | Run from the repository root and use `server.main:app` as the application target. |
| Port 8893 is already in use | Stop your previous server with **Ctrl+C**, or launch with `--port 8894` and open `http://127.0.0.1:8894`. |
| Browser cannot connect | Keep the server terminal open and inspect its final error. Check `http://127.0.0.1:8893/health`; a running server returns `{"status":"ok"}`. |
| Agent authentication fails | Check the selected provider's actual server credential, restart, and retry **Test Connection** before saving settings. |
| No valid plan is found | Check enabled drones, valid start/end airports, fuel budgets, target restrictions, and whether threats block all feasible routes. |
| Planning is slow | Try fewer targets or break the mission into segments. |

## Development checks

Python tests (from the root, after dependency installation):

```powershell
# Windows
.\.venv\Scripts\python.exe -m pytest
```

```bash
# macOS/Linux, with .venv active
python -m pytest
```

`pyproject.toml` supplies the test paths and Python import path. Live agent tests are opt-in via `ISR_RUN_LIVE_AGENT_TESTS=1` and require provider credentials.

Frontend checks require Node.js 22+. From the repository root on either OS:

```text
node scripts/build_page_shell.mjs --check
node scripts/check_frontend_syntax.mjs
npm ci --ignore-scripts
npm run install:browser
npm run test:infra
```

Generated HTML comes from `frontend/templates/`; after editing a template, run `node scripts/build_page_shell.mjs`. Additional Python formatting, lint, and type checks are available through `make check` where Make is installed.

## Deployment

`railway.toml` selects `Dockerfile.railway`, one replica, and the `/health` endpoint. To deploy this codebase, connect the intended repository and branch to a separate Railway service and configure its environment variables there. The existing demo URL does not automatically use this checkout.

Railway builds and runs dependencies remotely, so accessing a deployed application does not require local Python or Docker. The root `Dockerfile` and `docker-compose.yml` still refer to an older directory layout and need updates before local Docker use. Additional deployment notes are in [DEPLOYMENT.md](docs/session-notes/DEPLOYMENT.md).

## Project structure

```text
ISR-Planner-Production/
├── server/
│   ├── main.py                 # FastAPI app and static-file serving
│   ├── api/                    # API routes, models, and services
│   ├── agents/                 # Reasoning and multi-agent workflows
│   ├── solver/                 # Allocation, routing, and optimization
│   ├── database/               # Supabase integrations
│   ├── memory/                 # Session and constraint storage
│   └── gnn/                    # Graph neural network planning
├── webapp/                     # Browser UI, maps, and mission playback
├── frontend/                   # Shared HTML templates and page variants
├── path_planning_core/         # Threat-aware path planning
├── catalog/                    # Aircraft and sensor catalog
├── orienteering_with_matrix.py # Held–Karp orienteering solver
├── tests/                      # Python and frontend tests
├── scripts/                    # Build, check, and diagnostic utilities
├── docs/                       # Architecture, agents, solver, and history
├── requirements.txt            # Runtime and development dependencies
├── pyproject.toml              # Python project and pytest configuration
├── run_planner.sh              # macOS/Linux development launcher
├── Dockerfile.railway          # Railway container build
└── railway.toml                # Railway deployment configuration
```

Further reading: [architecture](docs/architecture/README.md), [agents](docs/agents/README.md), [solver](docs/solver/README.md), and [session notes](docs/session-notes/README.md). Session notes contain historical deployment and troubleshooting records; use the setup instructions above for a new local installation.
