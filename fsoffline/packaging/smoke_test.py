"""End-to-end smoke test of an installed desktop edition (Windows CI), also usable against ``fsoffline serve``.

    python smoke_test.py --data-dir C:\\ProgramData\\FSPlatform [--existing] [--url http://127.0.0.1:8746]

Waits (up to ``--wait`` seconds) for the app to start: its address is read from ``<data-dir>/runtime.json``.

First run, through the HTTP API exactly as the browser drives it:
  setup with the launcher's one-time token → the administrator signs in and enrols an authenticator → invites a
  colleague as Manager of a new client (the link is returned on screen) → the colleague accepts and enrols → client,
  entity and engagement → upload a synthetic workbook with known errors → checks → the colleague proposes the fix plan,
  the administrator (Partner) approves it → apply and verify → a LibreOffice draft PDF that reconciles with no misses →
  a watermarked download → a backup.
``--existing`` (after an upgrade or restore): the firm and the engagement are still there and both accounts sign in.

Uses only what the installed bundle contains (httpx, pyotp, fscore's synthetic workbook generator). State between the
two modes is kept in ``<data-dir>/smoke-test.json`` (test credentials for a throw-away CI machine only).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import pyotp

TIMEOUT = 240.0
REASON = {"X-Action-Reason": "CI smoke test of the installed desktop edition"}


class Step:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self, text: str) -> None:
        self.n += 1
        print(f"[{time.strftime('%H:%M:%S')}] {self.n:2d}. {text}", flush=True)


step = Step()


class User:
    """One signed-in browser: cookies, CSRF header and the authenticator."""

    def __init__(self, base: str, email: str, password: str, secret: str | None = None) -> None:
        self.c = httpx.Client(base_url=base, timeout=60.0, follow_redirects=False)
        self.email, self.password, self.secret = email, password, secret
        self.csrf = ""
        self._last_step = -1

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf, **(extra or {})}

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.c.get(path, **kw)

    def post(self, path: str, extra: dict[str, str] | None = None, **kw: Any) -> httpx.Response:
        return self.c.post(path, headers=self._headers(extra), **kw)

    def code(self) -> str:
        """A TOTP code from a time step not used before (the server refuses replays)."""
        assert self.secret
        totp = pyotp.TOTP(self.secret)
        while int(time.time() // 30) == self._last_step:
            time.sleep(1)
        self._last_step = int(time.time() // 30)
        return totp.now()

    def sign_in(self) -> list[str]:
        r = self.c.post("/api/v1/auth/login", json={"email": self.email, "password": self.password})
        expect(r, 200, "sign in")
        self.csrf = r.json()["csrf_token"]
        if r.json()["status"] == "mfa_enrollment_required":
            enrol = self.post("/api/v1/auth/mfa/enroll")
            expect(enrol, 200, "start authenticator enrolment")
            self.secret = enrol.json()["secret"]
            done = self.post("/api/v1/auth/mfa/enroll/confirm", json={"code": self.code()})
            expect(done, 200, "confirm authenticator")
            self.csrf = done.json()["csrf_token"]
            return list(done.json()["recovery_codes"])
        v = self.post("/api/v1/auth/mfa/verify", json={"code": self.code()})
        expect(v, 200, "authenticator code")
        self.csrf = v.json().get("csrf_token", self.csrf) or self.c.cookies.get("fs_csrf", self.csrf)
        return []

    def step_up(self) -> None:
        expect(self.post("/api/v1/auth/step-up", json={"code": self.code()}), 200, "step-up")


def expect(r: httpx.Response, status: int, what: str) -> httpx.Response:
    if r.status_code != status:
        raise SystemExit(f"FAILED: {what}: HTTP {r.status_code} {r.text[:400]}")
    return r


def wait_run(u: User, run_id: str, what: str) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        run = expect(u.get(f"/api/v1/runs/{run_id}"), 200, what).json()
        if run["status"] in ("succeeded", "failed", "cancelled"):
            if run["status"] != "succeeded":
                raise SystemExit(f"FAILED: {what}: run {run['status']}: {json.dumps(run.get('error') or run)[:600]}")
            return dict(run)
        time.sleep(1)
    raise SystemExit(f"FAILED: {what}: still running after {TIMEOUT:.0f}s")


def first_run(base: str, data_dir: Path, state_file: Path) -> None:
    step("first-run options")
    opts = expect(httpx.get(base + "/api/v1/auth/options"), 200, "options").json()
    assert opts["setup_required"], opts
    assert opts["edition"] == "desktop", opts
    assert not opts["sso"], opts
    token = (data_dir / "secrets" / "setup.token").read_text(encoding="ascii").strip()
    admin_email, colleague_email = "admin@corvus-advisors.example", "manager@corvus-advisors.example"
    admin_pw, colleague_pw = "Ledger-Smoke-Test-2026!a", "Ledger-Smoke-Test-2026!b"

    step("setup without the one-time token is refused")
    body = {"firm_name": "Corvus Advisors (CI)", "admin_name": "Asha Admin", "admin_email": admin_email, "password": admin_pw,
            "works_on_engagements": True}
    expect(httpx.post(base + "/api/v1/setup", json=body), 403, "setup without token")
    step("setup with the token")
    expect(httpx.post(base + "/api/v1/setup", json={**body, "setup_token": token}), 201, "setup")
    expect(httpx.post(base + "/api/v1/setup", json={**body, "setup_token": token}), 409, "second setup")

    step("administrator signs in and enrols an authenticator")
    admin = User(base, admin_email, admin_pw)
    assert len(admin.sign_in()) >= 8, "recovery codes"

    step("client, entity and engagement (the administrator becomes Partner on the client)")
    client = expect(admin.post("/api/v1/clients", json={"name": "Corvus Data Services (CI)"}), 201, "client").json()
    entity = expect(admin.post(f"/api/v1/clients/{client['id']}/entities", json={"name": "Corvus Data Services Private Limited"}), 201,
                    "entity").json()
    eng = expect(admin.post("/api/v1/engagements", json={"entity_id": entity["id"], "name": "FY 2025-26 (CI)", "type": "annual",
                                                         "period_end": "2026-03-31"}), 201, "engagement").json()

    step("invite a colleague as Manager of the client; the link is shown on screen")
    admin.step_up()
    inv = expect(admin.post("/api/v1/admin/users/invite", extra=REASON, json={"email": colleague_email, "name": "Manoj Manager",
                                                                              "role_code": "manager", "client_id": client["id"]}),
                 201, "invite").json()
    link = inv.get("invite_link")
    assert link, f"no invite link on screen: {inv}"
    assert "token=" in link, link
    expect(httpx.post(base + "/api/v1/auth/invite/accept", json={"token": link.split("token=", 1)[1], "password": colleague_pw}), 200,
           "accept invite")
    colleague = User(base, colleague_email, colleague_pw)
    colleague.sign_in()

    step("upload a synthetic workbook with known review errors")
    from fscore.synthetic.generator import make_workbook
    from fsweb.manage import DEMO_INJECTIONS

    with tempfile.TemporaryDirectory() as td:
        made = make_workbook(Path(td) / "Corvus FY26.xlsx", seed=7, inject=list(DEMO_INJECTIONS))
        data = Path(made.path).read_bytes()
    up = admin.post(f"/api/v1/engagements/{eng['id']}/files", data={"kind": "workbook"},
                    files={"file": ("Corvus FY26.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    expect(up, 201, "upload")

    step("checks")
    run = expect(admin.post(f"/api/v1/engagements/{eng['id']}/runs", json={"kind": "check"}), 202, "start checks").json()
    check = wait_run(admin, run["id"], "checks")
    findings = expect(admin.get(f"/api/v1/runs/{check['id']}/findings"), 200, "findings").json()
    highs = [f for f in findings if f["severity"] == "High"]
    assert highs, "the synthetic errors were not found"
    print(f"      {len(findings)} findings, {len(highs)} High")

    step("the colleague proposes the fix plan; the proposer cannot approve it")
    plan = expect(colleague.post(f"/api/v1/runs/{check['id']}/fix-plan"), 202, "propose").json()
    deadline = time.monotonic() + TIMEOUT
    while plan.get("status") != "proposed" and time.monotonic() < deadline:
        time.sleep(1)
        plan = expect(colleague.get(f"/api/v1/fix-plans/{plan['id']}"), 200, "plan").json()
    assert plan.get("status") == "proposed", plan
    items = [e["eid"] for e in plan["edits"] if e["fix_kind"] == "approval"]
    expect(colleague.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"], "approved_edit_ids": items}),
           403, "self-approval")

    step("the administrator (Partner) approves; the plan is applied and verified")
    expect(admin.post(f"/api/v1/fix-plans/{plan['id']}/approve", json={"plan_sha256": plan["plan_sha256"], "approved_edit_ids": items}),
           200, "approve")
    applied = expect(admin.post(f"/api/v1/fix-plans/{plan['id']}/apply"), 202, "apply").json()
    fixed = wait_run(admin, applied["id"], "apply and verify")
    verification = (fixed.get("result_json") or {}).get("verification") or {}
    assert verification.get("ok") is True, f"verification failed: {verification}"
    assert expect(admin.get(f"/api/v1/engagements/{eng['id']}"), 200, "engagement").json()["status"] == "FIXED_VERIFIED"

    step("draft PDF through the bundled LibreOffice reconciles with the workbook")
    rr = expect(admin.post(f"/api/v1/engagements/{eng['id']}/render", json={"mode": "draft", "engine": "libreoffice"}), 202, "render").json()
    render = wait_run(admin, rr["id"], "render")
    result = render.get("result_json") or {}
    misses = result.get("misses")
    assert result.get("engine") == "libreoffice", result
    assert (len(misses) if isinstance(misses, list) else int(misses or 0)) == 0, f"PDF does not match the workbook: {result}"
    print(f"      {result.get('pages')} pages, {result.get('engine_version')}")
    pdf_version = result.get("pdf_version_id") or render.get("output_version_id")
    dl = admin.get(f"/api/v1/versions/{pdf_version}/download")
    expect(dl, 200, "watermarked download")
    assert dl.content.startswith(b"%PDF"), "download is not a PDF"

    step("backup")
    proc = subprocess.run([sys.executable, "-X", "utf8", "-m", "fsoffline", "--data-dir", str(data_dir), "backup"],  # noqa: S603
                          capture_output=True, text=True, check=False)
    print("      " + (proc.stdout or proc.stderr).strip().replace("\n", "\n      "))
    if proc.returncode != 0:
        raise SystemExit("FAILED: backup")

    state_file.write_text(json.dumps({"engagement_id": eng["id"], "admin": [admin_email, admin_pw, admin.secret],
                                      "colleague": [colleague_email, colleague_pw, colleague.secret]}), encoding="utf-8")


def existing(base: str, state_file: Path) -> None:
    state = json.loads(state_file.read_text(encoding="utf-8"))
    step("the firm is still set up")
    opts = expect(httpx.get(base + "/api/v1/auth/options"), 200, "options").json()
    assert not opts["setup_required"], opts
    for who in ("admin", "colleague"):
        step(f"{who} signs in with the same authenticator")
        u = User(base, *state[who])
        u._last_step = int(time.time() // 30)  # the previous run may have used this time step: codes are single-use
        u.sign_in()
        eng = expect(u.get(f"/api/v1/engagements/{state['engagement_id']}"), 200, "engagement after upgrade").json()
        assert eng["status"] in ("FIXED_VERIFIED", "REVIEW"), eng


def wait_for_app(data_dir: Path, url: str | None, wait_s: float) -> str:
    """The app's address once it answers: ``url``, or the one the launcher wrote to runtime.json."""
    deadline = time.monotonic() + wait_s
    last = "runtime.json not written yet"
    while True:
        try:
            base = (url or json.loads((data_dir / "runtime.json").read_text(encoding="utf-8"))["url"]).rstrip("/")
            if httpx.get(base + "/api/v1/health", timeout=5).status_code == 200:
                return base
            last = f"{base} not healthy yet"
        except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
            last = f"{type(exc).__name__}: {exc}"
        if time.monotonic() > deadline:
            raise SystemExit(f"FS-Platform did not start within {wait_s:.0f} s ({last})")
        time.sleep(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", help="default: the address in <data-dir>/runtime.json (the app picks the next port if 8746 is taken)")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--existing", action="store_true", help="after an upgrade: the data is still there")
    ap.add_argument("--wait", type=float, default=180.0, help="seconds to wait for the app to start (default 180)")
    a = ap.parse_args()
    data_dir = Path(a.data_dir)
    base = wait_for_app(data_dir, a.url, a.wait)
    state_file = data_dir / "smoke-test.json"
    os.environ.setdefault("PYTHONUTF8", "1")
    if a.existing:
        existing(base, state_file)
    else:
        first_run(base, data_dir, state_file)
    print("SMOKE TEST PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
