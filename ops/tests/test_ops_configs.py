"""Static checks of the deployment configuration (no Docker / Helm / Terraform needed).

These guard the security properties the architecture depends on: network segregation (only the AI
gateway has egress), least-privilege DB roles, alert → runbook links, CERT-In log retention, edge
headers and allow-lists, and safe shell scripts.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

OPS = Path(__file__).resolve().parents[1]
ROOT = OPS.parent


def _yaml_files() -> list[Path]:
    files = [p for p in OPS.rglob("*") if p.suffix in {".yml", ".yaml"} and "templates" not in p.parts]
    files += list((ROOT / ".github" / "workflows").glob("*.yml"))
    return sorted(files)


@pytest.mark.parametrize("path", _yaml_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_yaml_parses(path: Path) -> None:
    docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    assert docs, f"{path} is empty"


# ----------------------------------------------------------------------------- compose
@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((OPS / "docker-compose.yml").read_text())


def test_compose_core_services(compose: dict) -> None:
    for name in ("caddy", "api", "worker", "ai-gateway", "migrate", "postgres", "minio", "minio-init"):
        assert name in compose["services"], name


def test_internal_network_has_no_internet_route(compose: dict) -> None:
    assert compose["networks"]["internal"]["internal"] is True


def test_only_designated_services_have_egress(compose: dict) -> None:
    allowed_egress = {"ai-gateway", "clamav", "backup", "alertmanager"}
    allowed_edge = {"caddy", "cloudflared"}
    for name, svc in compose["services"].items():
        nets = set(svc.get("networks", []))
        if "egress" in nets:
            assert name in allowed_egress, f"{name} must not have internet egress"
        if "edge" in nets:
            assert name in allowed_edge, f"{name} must not sit on the edge network"
    for name in ("api", "worker", "postgres", "minio", "redis", "migrate"):
        assert set(compose["services"][name]["networks"]) == {"internal"}, name


def test_only_caddy_publishes_ports(compose: dict) -> None:
    published = {name for name, svc in compose["services"].items() if svc.get("ports")}
    assert published == {"caddy"}


def test_runtime_uses_rls_role_and_migrations_use_owner(compose: dict) -> None:
    api_env = compose["services"]["api"]["environment"]
    assert "fsweb_app:" in api_env["FSWEB_DATABASE_URL"]
    assert "fsweb_owner" not in json.dumps(api_env)
    assert "fsweb_owner:" in compose["services"]["migrate"]["environment"]["FSWEB_DATABASE_URL"]
    assert "fsweb_owner" not in json.dumps(compose["services"]["worker"]["environment"])


def test_app_containers_hardened(compose: dict) -> None:
    for name in ("api", "worker", "ai-gateway", "migrate"):
        svc = compose["services"][name]
        assert svc.get("read_only") is True, name
        assert "ALL" in svc.get("cap_drop", []), name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name
    assert compose["services"]["api"]["environment"]["FSWEB_COOKIE_SECURE"] == "true"
    assert compose["services"]["api"]["environment"]["FSWEB_ENV"] == "prod"


def test_ai_disabled_by_default_and_key_only_in_gateway(compose: dict) -> None:
    assert "${FSWEB_AI_ENABLED:-false}" in compose["services"]["api"]["environment"]["FSWEB_AI_ENABLED"]
    for name, svc in compose["services"].items():
        env = svc.get("environment", {})
        if isinstance(env, dict) and "ANTHROPIC_API_KEY" in env:
            assert name == "ai-gateway"


def test_postgres_archives_wal_and_never_logs_dml(compose: dict) -> None:
    cmd = " ".join(compose["services"]["postgres"]["command"])
    assert "archive_mode=on" in cmd
    assert "archive_timeout=300" in cmd
    assert "log_statement=ddl" in cmd


def test_env_example_covers_compose_variables() -> None:
    text = (OPS / "docker-compose.yml").read_text() + (OPS / "docker-compose.ha.yml").read_text()
    required = set(re.findall(r"\$\{([A-Z0-9_]+):\?", text))
    example = {line.split("=", 1)[0] for line in (OPS / ".env.example").read_text().splitlines() if "=" in line and not line.startswith("#")}
    assert required <= example, sorted(required - example)


# ----------------------------------------------------------------------------- monitoring
REQUIRED_ALERTS = {
    "BackupJobFailed", "WalArchiveLagHigh", "OffsiteBackupStale", "RestoreDrillMissed", "DiskUsageHigh",
    "ApiLatencyP95High", "CheckRunP95High", "RenderP95High", "JobQueueBacklog", "ErrorBudgetBurn",
    "AuditChainVerificationFailed", "RenderWorkerDown", "ApiDown", "ClockNotSynchronised",
}


@pytest.fixture(scope="module")
def alert_rules() -> list[dict]:
    data = yaml.safe_load((OPS / "monitoring" / "alerts.yml").read_text())
    return [rule for group in data["groups"] for rule in group["rules"]]


def test_required_alerts_exist(alert_rules: list[dict]) -> None:
    names = {r["alert"] for r in alert_rules}
    assert not REQUIRED_ALERTS - names, sorted(REQUIRED_ALERTS - names)


def test_every_alert_links_an_existing_runbook(alert_rules: list[dict]) -> None:
    for rule in alert_rules:
        ann = rule["annotations"]
        assert rule["labels"]["severity"] in {"critical", "warning", "info"}
        assert ann["runbook"] == f"ops/runbooks/alerts/{rule['alert']}.md"
        assert (ROOT / ann["runbook"]).is_file(), ann["runbook"]
        assert ann["runbook_url"].endswith(f"/alerts/{rule['alert']}.md")
        assert ann.get("summary")
        assert ann.get("description")


def test_no_orphan_alert_runbooks(alert_rules: list[dict]) -> None:
    names = {r["alert"] for r in alert_rules}
    for runbook in (OPS / "runbooks" / "alerts").glob("*.md"):
        assert runbook.stem in names, f"{runbook.name} has no alert"


def test_prometheus_rule_files_exist() -> None:
    prom = yaml.safe_load((OPS / "monitoring" / "prometheus.yml").read_text())
    for rule_file in prom["rule_files"]:
        assert (OPS / "monitoring" / Path(rule_file).name).is_file()
    recording = yaml.safe_load((OPS / "monitoring" / "recording-rules.yml").read_text())
    recorded = {r["record"] for g in recording["groups"] for r in g["rules"]}
    alerts = (OPS / "monitoring" / "alerts.yml").read_text()
    for used in set(re.findall(r"fs:[a-z0-9_:]+", alerts)):
        assert used in recorded, used


def test_grafana_dashboard_valid() -> None:
    for path in (OPS / "monitoring" / "grafana" / "dashboards").glob("*.json"):
        dash = json.loads(path.read_text())
        ids = [p["id"] for p in dash["panels"]]
        assert len(ids) == len(set(ids))
        assert dash["uid"]


def test_loki_keeps_logs_180_days() -> None:
    loki = yaml.safe_load((OPS / "monitoring" / "loki-config.yml").read_text())
    retention = loki["limits_config"]["retention_period"]
    assert retention.endswith("h")
    assert int(retention[:-1]) >= 180 * 24
    assert loki["compactor"]["retention_enabled"] is True


def test_otel_collector_scrubs_sensitive_attributes() -> None:
    cfg = yaml.safe_load((OPS / "monitoring" / "otel-collector.yml").read_text())
    deleted = {a["key"] for a in cfg["processors"]["attributes/scrub"]["actions"] if a["action"] == "delete"}
    assert {"db.statement", "http.request.body", "url.query"} <= deleted
    for pipeline in ("traces", "logs"):
        assert "attributes/scrub" in cfg["service"]["pipelines"][pipeline]["processors"]


# ----------------------------------------------------------------------------- edge, time, IaC
def test_caddy_security_controls() -> None:
    caddy = (OPS / "caddy" / "Caddyfile").read_text()
    for needle in (
        "Strict-Transport-Security", "Content-Security-Policy", 'X-Frame-Options "DENY"', "frame-ancestors 'none'",
        "rate_limit", "respond /metrics 404", "/api/v1/admin", "not client_ip {$FS_OFFICE_CIDRS}",
        "/api/v1/internal/*", "{$FS_RENDER_WORKER_CIDRS}", "tls1.2 tls1.3", "X-Worker-Token delete",
    ):
        assert needle in caddy, needle


def test_chrony_uses_nic_and_npl() -> None:
    chrony = (OPS / "ntp" / "chrony.conf").read_text()
    assert "samay1.nic.in" in chrony
    assert "nplindia" in chrony


def test_helm_chart_files() -> None:
    chart = OPS / "helm" / "fs-platform"
    assert yaml.safe_load((chart / "Chart.yaml").read_text())["apiVersion"] == "v2"
    values = yaml.safe_load((chart / "values.yaml").read_text())
    assert values["api"]["replicas"] >= 2
    assert values["networkPolicy"]["enabled"] is True
    assert values["securityContext"]["readOnlyRootFilesystem"] is True
    for tpl in (chart / "templates").iterdir():
        text = tpl.read_text()
        assert text.count("{{") == text.count("}}"), tpl.name
        blocks = len(re.findall(r"{{-?\s*(?:if|with|range|define)\b", text))
        ends = len(re.findall(r"{{-?\s*end\b", text))
        assert blocks == ends, tpl.name


def test_terraform_guards() -> None:
    tf = OPS / "iac" / "terraform" / "azure-centralindia"
    for f in tf.glob("*.tf"):
        text = f.read_text()
        assert text.count("{") == text.count("}"), f.name
    variables = (tf / "variables.tf").read_text()
    assert "var.log_retention_days >= 180" in variables
    assert '["centralindia", "southindia", "westindia"]' in variables


# ----------------------------------------------------------------------------- scripts and timers
def _scripts() -> list[Path]:
    return sorted(p for p in OPS.rglob("*.sh"))


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: str(p.relative_to(OPS)))
def test_scripts_are_strict_and_executable(script: Path) -> None:
    text = script.read_text()
    assert text.startswith("#!/"), "missing shebang"
    if text.startswith("#!/bin/sh"):
        assert "set -eu" in text
    else:
        assert "set -euo pipefail" in text
    if os.name == "posix":
        assert os.access(script, os.X_OK), "not executable"
    if shutil.which("bash") and not text.startswith("#!/bin/sh"):
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_systemd_units_pair_and_escape_variables() -> None:
    units = OPS / "backup" / "systemd"
    for timer in units.glob("*.timer"):
        assert "OnCalendar=" in timer.read_text()
        assert (units / f"{timer.stem}.service").is_file(), timer.name
    for service in units.glob("*.service"):
        text = service.read_text()
        exec_line = next(line for line in text.splitlines() if line.startswith("ExecStart="))
        # systemd expands $VAR itself: shell variables must be written $$var
        assert not re.search(r"(?<!\$)\$[a-z_]", exec_line), exec_line


def test_secrets_never_in_examples() -> None:
    for example in (OPS / ".env.example", OPS / "backup" / "restic.env.example"):
        for line in example.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                if any(word in key for word in ("PASSWORD", "SECRET", "KEY", "TOKEN", "PASS")) and value:
                    assert value.startswith(("CHANGE_ME", "/run/secrets/")) or key in {
                        "MINIO_APP_ACCESS_KEY", "MINIO_KMS_SECRET_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                    } or "CHANGE_ME" in value, f"{example.name}: {key} looks like a real secret"
