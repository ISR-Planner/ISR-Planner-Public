"""Verify live-test gates in disposable projects with stubbed modules only."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_project(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    script = tests / "test_multi_agent_v4.py"
    shutil.copyfile(REPOSITORY / "tests/test_multi_agent_v4.py", script)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["."]\n'
        'markers = ["live_agent: opt-in live smoke tests"]\n'
    )
    # A private file exists, but no run may load it. The SDK/agent stack is stubbed.
    (tmp_path / ".env").write_text("OPENAI_API_KEY=synthetic-private-file-value\n")
    (tmp_path / "dotenv.py").write_text(
        "def load_dotenv(*args, **kwargs):\n"
        '    raise AssertionError("dotenv file access is forbidden")\n'
    )
    for directory in ["server", "server/database", "server/agents"]:
        package = tmp_path / directory
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text("")
    (tmp_path / "server/database/supabase_client.py").write_text(
        "_supabase_client = object()\n"
    )
    (tmp_path / "server/agents/isr_agent_multi_v4.py").write_text(
        "import json, os\nfrom pathlib import Path\n"
        "from server.database import supabase_client\n"
        "import dotenv\n"
        "dotenv.load_dotenv()  # Must be suppressed by the fixture before this import.\n"
        "def run_multi_agent_v4(**kwargs):\n"
        "    path = Path(os.environ['POLICY_RESULT'])\n"
        "    rows = json.loads(path.read_text()) if path.exists() else []\n"
        "    rows.append({\n"
        "        'dotenv_disabled': os.getenv('PYTHON_DOTENV_DISABLED') == '1',\n"
        "        'database_cleared': supabase_client._supabase_client is None,\n"
        "        'database_variables_absent': not os.getenv('SUPABASE_URL') and not os.getenv('SUPABASE_KEY'),\n"
        "        'exported_provider_retained': os.getenv('OPENAI_API_KEY') == 'synthetic-exported-value',\n"
        "    })\n"
        "    path.write_text(json.dumps(rows))\n"
        "    return {'response': 'neutral stub response'}\n"
    )
    result = tmp_path / "calls.json"
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(tmp_path),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "POLICY_RESULT": str(result),
        "OPENAI_API_KEY": "synthetic-exported-value",
        "SUPABASE_URL": "https://synthetic.invalid",
        "SUPABASE_KEY": "synthetic-storage-value",
    }
    return tmp_path, script, result, env


@pytest.mark.parametrize("standalone", [False, True])
def test_default_execution_never_reaches_live_agent(isolated_project, standalone):
    root, script, result, env = isolated_project
    command = (
        [sys.executable, str(script)]
        if standalone
        else [sys.executable, "-m", "pytest", "-q", str(script)]
    )
    completed = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, timeout=20
    )
    assert not result.exists()
    output = completed.stdout + completed.stderr
    if standalone:
        assert completed.returncode == 2
        assert "require --live" in output
    else:
        assert completed.returncode == 0
        assert "3 skipped" in output
    assert "synthetic-private-file-value" not in output
    assert "synthetic-exported-value" not in output
    assert "dotenv file access is forbidden" not in output


@pytest.mark.parametrize("standalone", [False, True])
def test_explicit_opt_in_uses_same_fixture_in_both_modes(isolated_project, standalone):
    root, script, result, env = isolated_project
    if standalone:
        command = [sys.executable, str(script), "--live"]
    else:
        env["ISR_RUN_LIVE_AGENT_TESTS"] = "1"
        command = [sys.executable, "-m", "pytest", "-q", str(script)]
    completed = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, timeout=20
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = json.loads(result.read_text())
    assert len(calls) == 3
    assert all(all(row.values()) for row in calls)
    output = completed.stdout + completed.stderr
    assert "synthetic-private-file-value" not in output
    assert "synthetic-exported-value" not in output
