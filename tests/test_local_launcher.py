"""Run the real local launcher in isolation, without starting/stopping a server."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("has_project_env", [True, False])
def test_local_launcher_configuration_and_uvicorn_command(tmp_path, has_project_env):
    project = tmp_path / "planner project"
    project.mkdir()
    launcher = project / "run_planner.sh"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "run_planner.sh", launcher)
    caller = tmp_path / "different working directory"
    caller.mkdir()
    # Neither a caller's file nor an ancestor's file may supply configuration.
    for directory in (tmp_path, caller):
        (directory / ".env").write_text("OPENAI_API_KEY=test-wrong-location-key\n")
    if has_project_env:
        (project / ".env").write_text(
            "OPENAI_API_KEY=test-project-key\n"
            "LLM_PROVIDER=google\n"
            "LLM_MODEL=test-project-model\n"
        )

    tools = tmp_path / "stub tools"
    tools.mkdir()
    # The real launcher's stop-old-process step must never inspect or stop an
    # actual listener. This stub emits no PID and records only its arguments.
    lsof = tools / "lsof"
    lsof.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$LAUNCH_TEST_LSOF"\nexit 1\n')
    lsof.chmod(0o755)
    python = tools / "python3"
    python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    python.chmod(0o755)

    # The normal python -m uvicorn entry point is replaced in this temporary
    # project only. It reports comparisons, never credential values.
    (project / "uvicorn.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['LAUNCH_TEST_RESULT']).write_text(json.dumps({\n"
        "  'argv': sys.argv[1:],\n"
        "  'cwd': os.getcwd(),\n"
        "  'project_key': os.getenv('OPENAI_API_KEY') == 'test-project-key',\n"
        "  'inherited_key': os.getenv('OPENAI_API_KEY') == 'test-inherited-key',\n"
        "  'provider': os.getenv('LLM_PROVIDER'),\n"
        "  'model': os.getenv('LLM_MODEL'),\n"
        "}))\n"
    )
    result_path = tmp_path / "launch-result.json"
    lsof_path = tmp_path / "lsof-arguments.txt"
    # Construct an isolated environment: no real shell credentials are copied.
    env = {
        "PATH": str(tools) + os.pathsep + os.defpath,
        "OPENAI_API_KEY": "test-inherited-key",
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "test-inherited-model",
        "LAUNCH_TEST_RESULT": str(result_path),
        "LAUNCH_TEST_LSOF": str(lsof_path),
    }
    completed = subprocess.run(
        ["/bin/bash", str(launcher)],
        cwd=caller,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )

    result = json.loads(result_path.read_text())
    assert lsof_path.read_text().splitlines() == ["-ti:8893", "-sTCP:LISTEN"]
    assert result["argv"] == ["server.main:app", "--reload", "--port", "8893"]
    assert result["cwd"] == str(project)
    assert result["project_key"] is has_project_env
    assert result["inherited_key"] is not has_project_env
    assert result["provider"] == ("google" if has_project_env else "openai")
    assert result["model"] == (
        "test-project-model" if has_project_env else "test-inherited-model"
    )
    output = completed.stdout + completed.stderr
    assert ".env configuration" in output
    assert "Stopping old planner" not in output
    for secret in ("test-project-key", "test-inherited-key", "test-wrong-location-key"):
        assert secret not in output
