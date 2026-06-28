"""Regression tests for entrypoint.sh static-file handling.

With DEBUG off (the secure default) the staticfiles storage is whitenoise's
manifest storage, which raises "Missing staticfiles manifest entry" unless
`collectstatic` has populated STATIC_ROOT. That requirement is independent of
PRESENCE_SERVER, so the entrypoint must run `collectstatic` for *every* server
mode -- not just gunicorn. Running it only on the gunicorn path made the
default `runserver` deployment return 500 on every HTML page.
"""
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[2] / "entrypoint.sh"


def _lines():
    return ENTRYPOINT.read_text().splitlines()


def test_collectstatic_runs_unconditionally_before_server_case():
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
