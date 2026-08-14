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


EXAMPLE_VALUES = REPO_ROOT / "helm" / "values.example.yaml"


def test_example_values_file_is_a_usable_starting_point():
    """The documented `-f` workflow must actually render.

    An example that does not work is worse than none, so this installs it
    exactly as the README says to: chart defaults overlaid with the example.
    """
    assert EXAMPLE_VALUES.is_file(), "helm/values.example.yaml must exist"
    docs = [
        doc
        for doc in yaml.safe_load_all(
            subprocess.run(
                ["helm", "template", "presence", str(CHART),
                 "--kube-version", KUBE_VERSION,
                 # Note: no BASE_ARGS — the example must stand on its own.
                 "-f", str(EXAMPLE_VALUES)],
                capture_output=True, text=True, timeout=120, check=True,
            ).stdout
        )
        if doc
    ]
    # It should demonstrate the deployment's actual shape, not the bare
    # defaults: published over TLS, compressed, with an admin login.
    assert by_kind(docs, "Ingress")
    assert by_kind(docs, "Middleware")
    web = one(docs, "Deployment", "-web")
    container = web["spec"]["template"]["spec"]["containers"][0]
    assert "DJANGO_SUPERUSER_PASSWORD" in env_refs(container)


def test_example_values_secrets_are_obvious_placeholders():
    """Nobody should be able to deploy the example's secrets by accident."""
    values = yaml.safe_load(EXAMPLE_VALUES.read_text())
    secrets = [
        values["django"]["secretKey"],
        values["django"]["superuser"]["password"],
        values["postgresql"]["password"],
    ]
    for secret in secrets:
        assert "CHANGE-ME" in secret, f"{secret!r} does not demand replacing"


def test_the_operators_own_values_file_is_not_committed():
    """The real file holds secrets, so it must be git-ignored...

    ...but the *chart's* own values.yaml must never be caught by that rule.
    """
    def ignored(path):
        return subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=REPO_ROOT, timeout=30,
        ).returncode == 0

    assert ignored("helm/values.yaml")
    assert not ignored("helm/values.example.yaml")
    assert not ignored("helm/presence/values.yaml")


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


CERT_MANAGER_ANNOTATION = "cert-manager.io/cluster-issuer"


def escaped(annotation):
    """Quote an annotation key for `helm --set`, which splits on dots."""
    return annotation.replace(".", "\\.")


def tls_args(*extra):
    return (
        "--set", "ingress.enabled=true",
        "--set", "ingress.tls.enabled=true",
        "--set", "ingress.tls.secretName=presence-tls",
        *extra,
    )


def test_tls_names_the_clusters_cert_manager_issuer_by_default():
    """The chart ships this cluster's issuer, which is named `acme`.

    Naming an issuer that does not exist fails quietly and completely:
    cert-manager parks the CertificateRequest on "ClusterIssuer not found",
    the Secret is never created, and Traefik then drops the whole Ingress
    rather than just its TLS — so the site serves Traefik's self-signed
    default certificate and 404s behind it.
    """
    ingress = one(render(*tls_args()), "Ingress")
    assert ingress["metadata"]["annotations"][CERT_MANAGER_ANNOTATION] == "acme"
    assert ingress["spec"]["tls"][0]["secretName"] == "presence-tls"


def test_cert_manager_issuer_is_configurable():
    docs = render(*tls_args(
        "--set", "ingress.tls.clusterIssuer=letsencrypt-staging",
    ))
    annotations = one(docs, "Ingress")["metadata"]["annotations"]
    assert annotations[CERT_MANAGER_ANNOTATION] == "letsencrypt-staging"


def test_no_issuer_annotation_without_tls():
    ingress = one(render("--set", "ingress.enabled=true"), "Ingress")
    assert CERT_MANAGER_ANNOTATION not in (
        ingress["metadata"].get("annotations") or {}
    )


def test_a_blank_issuer_leaves_the_certificate_to_the_operator():
    """cert-manager is not compulsory — a hand-managed Secret needs no issuer."""
    docs = render(*tls_args("--set", "ingress.tls.clusterIssuer="))
    ingress = one(docs, "Ingress")
    assert CERT_MANAGER_ANNOTATION not in (
        ingress["metadata"].get("annotations") or {}
    )
    # The Secret is still referenced; only the issuance request is dropped.
    assert ingress["spec"]["tls"][0]["secretName"] == "presence-tls"


def test_an_explicit_issuer_annotation_is_not_overwritten():
    """As with the middleware reference, the operator's own value wins."""
    docs = render(*tls_args(
        "--set", f"ingress.annotations.{escaped(CERT_MANAGER_ANNOTATION)}=mine",
    ))
    annotations = one(docs, "Ingress")["metadata"]["annotations"]
    assert annotations[CERT_MANAGER_ANNOTATION] == "mine"


def csrf_origins(docs):
    web = one(docs, "Deployment", "-web")
    env = container_env(web["spec"]["template"]["spec"]["containers"][0])
    return env["DJANGO_CSRF_TRUSTED_ORIGINS"].split(",")


def test_csrf_origin_carries_a_non_standard_public_port():
    """The browser sends the port in its Origin header, so Django's trusted
    origin must carry it too.

    This deployment's router cannot forward :443, so the app is published on
    :8443 and every login POST would otherwise fail the CSRF origin check.
    """
    docs = render(*tls_args(
        "--set", "ingress.host=presence.example.com",
        "--set", "ingress.publicPort=8443",
    ))
    assert csrf_origins(docs) == ["https://presence.example.com:8443"]

    # ALLOWED_HOSTS is compared without the port, so it must not gain one.
    web = one(docs, "Deployment", "-web")
    hosts = container_env(
        web["spec"]["template"]["spec"]["containers"][0]
    )["DJANGO_ALLOWED_HOSTS"].split(",")
    assert "presence.example.com" in hosts
    assert not [h for h in hosts if ":8443" in h]


@pytest.mark.parametrize("port", ["0", "443"])
def test_the_default_https_port_is_left_off_the_csrf_origin(port):
    """Browsers omit the scheme's default port, so an origin carrying it
    would never match. Zero means "the default", as it does in the chart."""
    docs = render(*tls_args("--set", f"ingress.publicPort={port}"))
    assert csrf_origins(docs) == ["https://presence.hopto.org"]


@pytest.mark.parametrize("port", ["0", "80"])
def test_the_default_http_port_is_left_off_the_csrf_origin(port):
    """Without TLS the default is 80, not 443."""
    docs = render(
        "--set", "ingress.enabled=true",
        "--set", f"ingress.publicPort={port}",
    )
    assert "http://presence.hopto.org" in csrf_origins(docs)
    assert not [o for o in csrf_origins(docs) if ":80" in o]


MIDDLEWARE_ANNOTATION = "traefik.ingress.kubernetes.io/router.middlewares"


def test_no_compression_middleware_unless_enabled():
    """Off by default: the Middleware CRD only exists on Traefik clusters."""
    docs = render("--set", "ingress.enabled=true")
    assert not by_kind(docs, "Middleware")
    ingress = one(docs, "Ingress")
    assert MIDDLEWARE_ANNOTATION not in (
        ingress["metadata"].get("annotations") or {}
    )


def test_compression_renders_a_traefik_middleware_and_wires_it_up():
    """Replaces the compose stack's Caddy `encode zstd gzip`."""
    docs = render(
        "--set", "ingress.enabled=true",
        "--set", "compression.enabled=true",
    )
    middleware = one(docs, "Middleware")
    # The Traefik v3 API group; v2's traefik.containo.us is long gone.
    assert middleware["apiVersion"] == "traefik.io/v1alpha1"
    assert "compress" in middleware["spec"]

    # ...and the Ingress must actually reference it, or it does nothing.
    ingress = one(docs, "Ingress")
    ref = ingress["metadata"]["annotations"][MIDDLEWARE_ANNOTATION]
    assert ref.endswith("@kubernetescrd")
    assert middleware["metadata"]["name"] in ref


def test_compression_options_are_configurable():
    docs = render(
        "--set", "ingress.enabled=true",
        "--set", "compression.enabled=true",
        "--set", "compression.minResponseBodyBytes=2048",
        "--set", "compression.encodings={gzip}",
    )
    compress = one(docs, "Middleware")["spec"]["compress"]
    assert compress["minResponseBodyBytes"] == 2048
    assert compress["encodings"] == ["gzip"]


def test_compression_annotation_does_not_clobber_the_operators_own():
    """The annotation is a comma-separated list, so ours must append."""
    docs = render(
        "--set", "ingress.enabled=true",
        "--set", "compression.enabled=true",
        "--set", "ingress.annotations.cert-manager\\.io/cluster-issuer=le",
        "--set", f"ingress.annotations.{MIDDLEWARE_ANNOTATION.replace('.', '\\.')}=myauth@kubernetescrd",
    )
    annotations = one(docs, "Ingress")["metadata"]["annotations"]
    # An unrelated annotation (e.g. cert-manager's) survives untouched.
    assert annotations["cert-manager.io/cluster-issuer"] == "le"
    refs = annotations[MIDDLEWARE_ANNOTATION].split(",")
    assert "myauth@kubernetescrd" in refs
    assert any(r.endswith("-compress@kubernetescrd") for r in refs)


def test_compression_without_an_ingress_is_refused():
    """It works by annotating the Ingress, so it cannot act without one."""
    result = subprocess.run(
        ["helm", "template", "presence", str(CHART),
         "--kube-version", KUBE_VERSION, *BASE_ARGS,
         "--set", "compression.enabled=true"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "ingress" in (result.stdout + result.stderr).lower()


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
