"""Tests for the Kubernetes Helm chart (issue #62).

The chart in `helm/presence` is the deployment contract for k3s/Kubernetes,
and it has to preserve the same invariants the docker-compose deployment
does — the ones documented in CLAUDE.md:

- **Exactly one runner** may drive a given database. In Kubernetes that means
  `replicas: 1` *and* the `Recreate` update strategy: a rolling update would
  otherwise start the new runner pod before the old one terminates, briefly
  running two state machines against one database.
- The **web** role stays single-worker/single-replica: the failed-login and
  API rate limiter uses an in-process `LocMemCache` that is only coherent
  within one process.
- `migrate` has a **single owner** — the web entrypoint. The runner must
  therefore wait for the schema rather than migrate concurrently.
- Secrets (Django secret key, database and superuser passwords, the W3W API
  key) belong in a Secret, never in the ConfigMap.

Rather than assert on template text, these tests render the chart with
`helm template` and inspect the resulting objects, so they check what
Kubernetes would actually be given. They skip when helm is not installed;
CI runs `helm lint`/`helm template` explicitly in the `helm` job of
`.github/workflows/build.yml`.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "helm" / "presence"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

# The chart targets Kubernetes v1.36 and beyond (issue #62); render against
# that version so a template using an API removed by then would fail here.
KUBE_VERSION = "1.36.0"

# A secret key is mandatory (the app refuses to boot without one when DEBUG
# is off), so every render has to supply one.
BASE_ARGS = ["--set", "django.secretKey=test-only-not-a-real-secret"]

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm is not installed"
)


def render(*extra_args):
    """Render the chart and return the parsed documents as a list of dicts."""
    result = subprocess.run(
        [
            "helm",
            "template",
            "presence",
            str(CHART),
            "--kube-version",
            KUBE_VERSION,
            *BASE_ARGS,
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def by_kind(docs, kind, name_suffix=None):
    """Return the documents of `kind`, optionally filtered by name suffix."""
    found = [d for d in docs if d.get("kind") == kind]
    if name_suffix is not None:
        found = [
            d
            for d in found
            if d["metadata"]["name"].endswith(name_suffix)
        ]
    return found


def one(docs, kind, name_suffix=None):
    found = by_kind(docs, kind, name_suffix)
    assert len(found) == 1, (
        f"expected exactly one {kind} "
        f"{'ending in ' + name_suffix if name_suffix else ''}, "
        f"got {[d['metadata']['name'] for d in found]}"
    )
    return found[0]


def container_env(container):
    """Flatten a container's literal env vars into a name -> value dict."""
    return {
        e["name"]: e["value"]
        for e in container.get("env", [])
        if "value" in e
    }


def env_refs(container):
    """Return name -> valueFrom mapping for env sourced from elsewhere."""
    return {
        e["name"]: e["valueFrom"]
        for e in container.get("env", [])
        if "valueFrom" in e
    }


# --- the chart exists and is well-formed ---------------------------------


def test_chart_metadata_is_present():
    chart_yaml = CHART / "Chart.yaml"
    assert chart_yaml.is_file(), "helm/presence/Chart.yaml must exist"
    meta = yaml.safe_load(chart_yaml.read_text())
    assert meta["apiVersion"] == "v2"
    assert meta["name"] == "presence"
    # Version numbers are semver without a "v" prefix.
    assert not meta["version"].startswith("v")


def test_default_image_tag_is_a_published_release():
    """appVersion is the default image tag, so it must name a real release.

    The tag comes from the newest git release tag (what the release workflow
    publishes to Docker Hub) — deliberately not pyproject.toml's version,
    which nothing reads and which has drifted behind the releases.
    """
    tags = subprocess.run(
        ["git", "tag", "--sort=-v:refname"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    newest = tags.stdout.split("\n")[0].strip() if tags.returncode == 0 else ""
    if not newest:
        pytest.skip("no git tags in this checkout (e.g. a shallow clone)")
    meta = yaml.safe_load((CHART / "Chart.yaml").read_text())
    assert meta["appVersion"] == newest, (
        f"Chart appVersion {meta['appVersion']!r} is not the newest release "
        f"tag {newest!r}, so the chart's default image tag may not exist"
    )


def test_chart_lints_cleanly():
    result = subprocess.run(
        ["helm", "lint", str(CHART), "--kube-version", KUBE_VERSION,
         *BASE_ARGS],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_secret_key_is_required():
    """Rendering without a secret key (or an existing Secret) must fail."""
    result = subprocess.run(
        # Note: no BASE_ARGS, so no secret key is supplied.
        ["helm", "template", "presence", str(CHART),
         "--kube-version", KUBE_VERSION],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "secretKey" in result.stdout + result.stderr


def test_chart_declares_its_kubernetes_floor():
    """The chart targets v1.36+; older clusters must be refused up front."""
    result = subprocess.run(
        ["helm", "template", "presence", str(CHART),
         "--kube-version", "1.35.0", *BASE_ARGS],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "kubeVersion" in result.stdout + result.stderr


# --- the runner invariant ------------------------------------------------


def test_runner_is_a_single_replica_that_never_overlaps_itself():
    docs = render()
    runner = one(docs, "Deployment", "-runner")
    assert runner["spec"]["replicas"] == 1
    # Recreate, not RollingUpdate: a surge would run two state machines
    # against one database during an upgrade.
    assert runner["spec"]["strategy"]["type"] == "Recreate"


def test_runner_replicas_cannot_be_raised_from_values():
    """The single-runner invariant must not be a value the operator can bump."""
    docs = render("--set", "runner.replicaCount=3")
    runner = one(docs, "Deployment", "-runner")
    assert runner["spec"]["replicas"] == 1


def test_runner_runs_the_runner_role_and_serves_no_http():
    docs = render()
    runner = one(docs, "Deployment", "-runner")
    container = runner["spec"]["template"]["spec"]["containers"][0]
    assert container_env(container)["PRESENCE_SERVER"] == "runner"
    assert not container.get("ports"), "the runner serves no HTTP"
    # No Service should select the runner pods.
    runner_labels = runner["spec"]["selector"]["matchLabels"]
    for service in by_kind(docs, "Service"):
        assert service["spec"].get("selector") != runner_labels


def test_runner_waits_for_the_schema_instead_of_migrating():
    """Web owns `migrate`; the runner waits for it rather than racing it."""
    docs = render()
    runner = one(docs, "Deployment", "-runner")
    spec = runner["spec"]["template"]["spec"]
    init = spec.get("initContainers") or []
    assert init, "the runner needs an init container that waits for migrations"
    joined = json.dumps(init)
    assert "migrate --check" in joined, (
        "the runner should wait on `migrate --check` (a read-only probe) "
        "rather than applying migrations itself"
    )
    # And it must never apply them.
    runner_container = spec["containers"][0]
    assert "migrate" not in json.dumps(runner_container.get("command", []))


# --- the web role --------------------------------------------------------


def test_web_is_a_single_replica_running_gunicorn():
    docs = render()
    web = one(docs, "Deployment", "-web")
    # The in-process ratelimit cache is only coherent within one process,
    # so neither the replica count nor the worker count may exceed one.
    assert web["spec"]["replicas"] == 1
    assert web["spec"]["strategy"]["type"] == "Recreate"
    container = web["spec"]["template"]["spec"]["containers"][0]
    env = container_env(container)
    assert env["PRESENCE_SERVER"] == "gunicorn"
    # The web pod must never also spawn the in-process runner thread.
    assert env["PRESENCE_RUN_RUNNER"] == "false"


def test_web_waits_for_the_database_before_starting():
    """Without this the web pod crash-loops until PostgreSQL accepts calls.

    Observed on a real cluster: the entrypoint's `migrate` runs the moment
    the container starts, and a database that is not yet resolvable kills
    it — two restarts before the pod settled. This is the chart's equivalent
    of compose's `depends_on: db condition: service_healthy`.
    """
    docs = render()
    web = one(docs, "Deployment", "-web")
    init = web["spec"]["template"]["spec"].get("initContainers") or []
    assert init, "the web pod needs an init container that waits for the DB"
    joined = json.dumps(init)
    # It waits for a connection; applying migrations stays the job of the
    # web container's own entrypoint, which owns them.
    assert "create_connection" in joined
    assert "migrate" not in joined


def test_web_replicas_cannot_be_raised_from_values():
    docs = render("--set", "web.replicaCount=4")
    web = one(docs, "Deployment", "-web")
    assert web["spec"]["replicas"] == 1


def test_web_and_runner_share_one_image():
    docs = render("--set", "image.tag=1.2.3")
    web = one(docs, "Deployment", "-web")
    runner = one(docs, "Deployment", "-runner")
    web_image = web["spec"]["template"]["spec"]["containers"][0]["image"]
    runner_image = runner["spec"]["template"]["spec"]["containers"][0]["image"]
    assert web_image == runner_image
    assert web_image.endswith(":1.2.3")


def test_web_service_targets_the_app_port():
    docs = render()
    service = one(docs, "Service", "-web")
    port = service["spec"]["ports"][0]
    assert port["targetPort"] == "http"
    web = one(docs, "Deployment", "-web")
    container = web["spec"]["template"]["spec"]["containers"][0]
    assert container["ports"][0]["name"] == "http"
    assert container["ports"][0]["containerPort"] == 8000


def test_web_probes_send_a_host_django_allows():
    """Probes hit the pod IP, which ALLOWED_HOSTS would otherwise reject."""
    docs = render()
    web = one(docs, "Deployment", "-web")
    container = web["spec"]["template"]["spec"]["containers"][0]
    for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
        if probe not in container:
            continue
        headers = container[probe]["httpGet"].get("httpHeaders", [])
        hosts = [h["value"] for h in headers if h["name"] == "Host"]
        assert hosts, f"{probe} must set a Host header"
        env = container_env(container)
        assert hosts[0] in env["DJANGO_ALLOWED_HOSTS"].split(",")


# --- configuration -------------------------------------------------------


def test_ingress_host_reaches_allowed_hosts_and_csrf_origins():
    docs = render(
        "--set", "ingress.enabled=true",
        "--set", "ingress.host=presence.example.com",
    )
    ingress = one(docs, "Ingress")
    assert ingress["apiVersion"] == "networking.k8s.io/v1"
    rule = ingress["spec"]["rules"][0]
    assert rule["host"] == "presence.example.com"
    backend = rule["http"]["paths"][0]["backend"]["service"]
    assert backend["name"] == one(docs, "Service", "-web")["metadata"]["name"]

    web = one(docs, "Deployment", "-web")
    env = container_env(web["spec"]["template"]["spec"]["containers"][0])
    assert "presence.example.com" in env["DJANGO_ALLOWED_HOSTS"].split(",")
    assert (
        "https://presence.example.com"
        in env["DJANGO_CSRF_TRUSTED_ORIGINS"].split(",")
    )


def test_ingress_defaults_to_the_deployment_host_on_traefik():
    """The chart ships this deployment's own defaults, not placeholders."""
    docs = render("--set", "ingress.enabled=true")
    ingress = one(docs, "Ingress")
    assert ingress["spec"]["ingressClassName"] == "traefik"
    assert ingress["spec"]["rules"][0]["host"] == "presence.hopto.org"

    web = one(docs, "Deployment", "-web")
    env = container_env(web["spec"]["template"]["spec"]["containers"][0])
    assert "presence.hopto.org" in env["DJANGO_ALLOWED_HOSTS"].split(",")


def test_no_ingress_unless_enabled():
    assert not by_kind(render(), "Ingress")


def test_no_sensitive_value_appears_outside_the_secret():
    docs = render(
        "--set", "django.secretKey=super-secret-value",
        "--set", "postgresql.password=pg-secret-value",
        "--set", "django.superuser.password=admin-secret-value",
        "--set", "w3w.apiKey=w3w-secret-value",
    )
    assert by_kind(docs, "Secret"), "secrets must be held in a Secret"
    for doc in docs:
        if doc.get("kind") == "Secret":
            continue
        rendered = json.dumps(doc)
        for leaked in (
            "super-secret-value",
            "pg-secret-value",
            "admin-secret-value",
            "w3w-secret-value",
        ):
            assert leaked not in rendered, (
                f"{leaked!r} leaked into "
                f"{doc['kind']}/{doc['metadata']['name']}"
            )


def test_sensitive_env_comes_from_the_secret():
    docs = render()
    for suffix in ("-web", "-runner"):
        deployment = one(docs, "Deployment", suffix)
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        refs = env_refs(container)
        assert "DJANGO_SECRET_KEY" in refs
        assert "secretKeyRef" in refs["DJANGO_SECRET_KEY"]
        assert "DJANGO_DB_PASSWORD" in refs
        assert "secretKeyRef" in refs["DJANGO_DB_PASSWORD"]
        # ...and never as a literal.
        assert "DJANGO_SECRET_KEY" not in container_env(container)


def test_existing_secret_replaces_the_generated_one():
    docs = render(
        "--set", "django.existingSecret=my-presence-secret",
        # The chart must not demand an inline key when one is supplied.
        "--set", "django.secretKey=",
    )
    assert not by_kind(docs, "Secret", "-secret")
    web = one(docs, "Deployment", "-web")
    container = web["spec"]["template"]["spec"]["containers"][0]
    ref = env_refs(container)["DJANGO_SECRET_KEY"]["secretKeyRef"]
    assert ref["name"] == "my-presence-secret"


def test_debug_is_off_by_default():
    docs = render()
    web = one(docs, "Deployment", "-web")
    env = container_env(web["spec"]["template"]["spec"]["containers"][0])
    assert env["DJANGO_DEBUG"] == "False"


def test_superuser_is_only_created_when_a_password_is_supplied():
    without = render()
    web = one(without, "Deployment", "-web")
    container = web["spec"]["template"]["spec"]["containers"][0]
    assert "DJANGO_SUPERUSER_PASSWORD" not in env_refs(container)
    assert "DJANGO_SUPERUSER_USERNAME" not in container_env(container)

    with_pw = render("--set", "django.superuser.password=s3cret-test-only")
    web = one(with_pw, "Deployment", "-web")
    container = web["spec"]["template"]["spec"]["containers"][0]
    assert "DJANGO_SUPERUSER_PASSWORD" in env_refs(container)
    assert container_env(container)["DJANGO_SUPERUSER_USERNAME"] == "admin"
    # The runner never creates a superuser.
    runner = one(with_pw, "Deployment", "-runner")
    runner_container = runner["spec"]["template"]["spec"]["containers"][0]
    assert "DJANGO_SUPERUSER_PASSWORD" not in env_refs(runner_container)


# --- the database --------------------------------------------------------


def test_bundled_postgresql_is_a_stateful_set_with_a_volume():
    docs = render()
    sts = one(docs, "StatefulSet")
    assert sts["spec"]["replicas"] == 1
    claims = sts["spec"]["volumeClaimTemplates"]
    assert claims, "the database needs persistent storage"
    service = one(docs, "Service", "-postgresql")
    assert service["spec"]["ports"][0]["port"] == 5432


def test_app_points_at_the_bundled_database():
    docs = render()
    db_service = one(docs, "Service", "-postgresql")["metadata"]["name"]
    for suffix in ("-web", "-runner"):
        deployment = one(docs, "Deployment", suffix)
        env = container_env(
            deployment["spec"]["template"]["spec"]["containers"][0]
        )
        assert env["DJANGO_DB_HOST"] == db_service
        assert env["DJANGO_DB_PORT"] == "5432"


def test_external_database_replaces_the_bundled_one():
    docs = render(
        "--set", "postgresql.enabled=false",
        "--set", "externalDatabase.host=db.internal",
        "--set", "externalDatabase.port=6432",
        "--set", "externalDatabase.password=external-secret",
    )
    assert not by_kind(docs, "StatefulSet")
    web = one(docs, "Deployment", "-web")
    env = container_env(web["spec"]["template"]["spec"]["containers"][0])
    assert env["DJANGO_DB_HOST"] == "db.internal"
    assert env["DJANGO_DB_PORT"] == "6432"


# --- ARM / k3s -----------------------------------------------------------


def test_release_workflow_still_publishes_an_arm64_image():
    """k3s targets are ARM, so the published image must be multi-arch."""
    workflow = RELEASE_WORKFLOW.read_text()
    assert "linux/arm64" in workflow
    assert "linux/amd64" in workflow


def test_node_selector_and_tolerations_are_configurable():
    docs = render(
        "--set", "nodeSelector.kubernetes\\.io/arch=arm64",
    )
    for suffix in ("-web", "-runner"):
        spec = one(docs, "Deployment", suffix)["spec"]["template"]["spec"]
        assert spec["nodeSelector"] == {"kubernetes.io/arch": "arm64"}
