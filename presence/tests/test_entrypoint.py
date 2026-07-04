"""Tests for entrypoint.sh role handling.

Static-file regression (original): with DEBUG off the staticfiles storage is
whitenoise's manifest storage, which raises "Missing staticfiles manifest
entry" unless `collectstatic` has populated STATIC_ROOT. That holds for every
*web* server mode, so the entrypoint must run `collectstatic` for runserver
as well as gunicorn.

Per-role startup (issue #47): the same image also runs the dedicated runner
container (`PRESENCE_SERVER=runner`). The one-time pre-steps — `migrate`,
conditional `createsuperuser`, `collectstatic` — belong to the web roles
only; the runner must skip them (migrations get a single owner, avoiding a
concurrent-migrate race) and exec the `run_runner` management command.

The behavioural tests run the real script with stub `python` / `gunicorn`
executables on PATH that just log their arguments.
"""
import os
import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[2] / "entrypoint.sh"

_STUB = '#!/usr/bin/env bash\necho "$(basename "$0") $*" >> "$CALL_LOG"\n'


def _lines():
    return ENTRYPOINT.read_text().splitlines()


def _run_entrypoint(tmp_path, server_mode, extra_env=None):
    """Execute entrypoint.sh with stubbed executables; return (result, log)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    call_log = tmp_path / "calls.log"
    call_log.touch()
    for name in ("python", "gunicorn"):
        stub_path = bin_dir / name
        stub_path.write_text(_STUB)
        stub_path.chmod(0o755)

    env = {k: v for k, v in os.environ.items()}
    # Deterministic: no leaked superuser vars from the developer's shell.
    env.pop("DJANGO_SUPERUSER_USERNAME", None)
    env.pop("DJANGO_SUPERUSER_PASSWORD", None)
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "CALL_LOG": str(call_log),
            "PRESENCE_SERVER": server_mode,
        }
    )
    env.update(extra_env or {})

    result = subprocess.run(
        ["bash", str(ENTRYPOINT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, call_log.read_text()


def test_collectstatic_runs_before_server_case_for_web_roles():
    lines = _lines()
    collect_idx = [i for i, ln in enumerate(lines) if "collectstatic" in ln]
    case_idx = [
        i for i, ln in enumerate(lines) if 'case "${PRESENCE_SERVER' in ln
    ]
    assert collect_idx, "entrypoint must run collectstatic"
    assert case_idx, "entrypoint must branch on PRESENCE_SERVER"
    # collectstatic must run before (and outside) the server case so that
    # runserver and gunicorn alike get a populated manifest.
    assert min(collect_idx) < case_idx[0], (
        "collectstatic must run before the PRESENCE_SERVER case so it applies "
        "to runserver too, not only gunicorn"
    )


def test_runserver_role_runs_presteps_then_server(tmp_path):
    result, log = _run_entrypoint(tmp_path, "runserver")
    assert result.returncode == 0, result.stderr
    assert "manage.py migrate" in log
    assert "manage.py collectstatic" in log
    assert "manage.py runserver" in log
    # Pre-steps must precede the server start.
    assert log.index("migrate") < log.index("runserver")


def test_gunicorn_role_runs_presteps_then_single_worker_server(tmp_path):
    result, log = _run_entrypoint(tmp_path, "gunicorn")
    assert result.returncode == 0, result.stderr
    assert "manage.py migrate" in log
    assert "manage.py collectstatic" in log
    # --workers 1 is mandatory: the in-process ratelimit cache is only
    # coherent within a single web process.
    assert "gunicorn" in log
    assert "--workers 1" in log


def test_runner_role_skips_presteps_and_execs_run_runner(tmp_path):
    result, log = _run_entrypoint(tmp_path, "runner")
    assert result.returncode == 0, result.stderr
    assert "manage.py run_runner" in log
    # Migrations have a single owner (web); the runner container must not
    # race it, and it serves no HTTP so needs no static files or superuser.
    assert "migrate" not in log
    assert "collectstatic" not in log
    assert "createsuperuser" not in log


def test_superuser_created_for_web_but_not_runner(tmp_path):
    creds = {
        "DJANGO_SUPERUSER_USERNAME": "admin",
        "DJANGO_SUPERUSER_PASSWORD": "s3cret-test-only",
    }
    _, web_log = _run_entrypoint(tmp_path / "web", "runserver", creds)
    _, runner_log = _run_entrypoint(tmp_path / "runner", "runner", creds)
    assert "createsuperuser" in web_log
    assert "createsuperuser" not in runner_log


def test_unknown_role_fails_and_names_runner_in_allowed_list(tmp_path):
    result, _ = _run_entrypoint(tmp_path, "bogus")
    assert result.returncode != 0
    assert "runserver" in result.stderr
    assert "gunicorn" in result.stderr
    assert "runner" in result.stderr
