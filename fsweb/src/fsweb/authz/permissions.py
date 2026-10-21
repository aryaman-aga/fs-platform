"""Roles, permissions and the permission matrix of 05 §2–§3, as seed data.

The matrix is data (``role_permission.value``) so a Firm Admin can edit it; this
module holds the default. Cell values:

* ``allow``           – ✓
* ``maker_checker``   – M: allowed, but the result needs a different user's approval
* ``not_own``         – ✓ (not own plan / request): allowed except on own items
* ``shared_only``     – client roles: only items shared with the client
* ``role_setting``    – governed by the role's download setting
* ``policy``          – ✓ (policy): allowed when the firm's AI policy permits
* ``own_clients``     – ✓ (own clients): only clients the user is assigned to
* ``dual``            – ✓ + Firm Admin (dual): needs a Firm Admin co-approval
* ``approve_restore`` – may approve a restore of client data, not run backups
* absent              – – (denied)
"""

from __future__ import annotations

from dataclasses import dataclass

ALLOW = "allow"
MAKER_CHECKER = "maker_checker"
NOT_OWN = "not_own"
SHARED_ONLY = "shared_only"
ROLE_SETTING = "role_setting"
POLICY = "policy"
OWN_CLIENTS = "own_clients"
DUAL = "dual"
APPROVE_RESTORE = "approve_restore"
DENY = "deny"

# Strength order when a user holds several roles on the same client.
STRENGTH = [ALLOW, OWN_CLIENTS, POLICY, NOT_OWN, MAKER_CHECKER, DUAL, APPROVE_RESTORE, SHARED_ONLY, ROLE_SETTING, DENY]


@dataclass(frozen=True)
class RoleDef:
    code: str
    name: str
    scope: str  # tenant | client
    is_client_role: bool
    download_allowed: bool
    description: str


ROLES: list[RoleDef] = [
    RoleDef("system_admin", "System Admin", "tenant", False, False, "Infrastructure, backups, updates; no client data (break-glass only)"),
    RoleDef("firm_admin", "Firm Admin", "tenant", False, False, "Users, roles, clients, assignment, policies, AI settings"),
    RoleDef("partner", "Partner", "client", False, True, "Approves final, locks engagement, signs off"),
    RoleDef("manager", "Manager", "client", False, True, "Approves fix plans, AI calls and releases drafts"),
    RoleDef("staff", "Staff / Article", "client", False, True, "Uploads, runs checks, proposes fixes, edits typed text"),
    RoleDef("reviewer", "Reviewer (read-only)", "client", False, True, "EQCR / internal QC: views, raises review notes"),
    RoleDef("client_uploader", "Client – Uploader", "client", True, True, "Uploads TB / workbooks, answers queries"),
    RoleDef("client_viewer", "Client – Viewer", "client", True, False, "Views released drafts and finals, confirms representations"),
]
ROLE_CODES = [r.code for r in ROLES]
FIRM_CLIENT_ROLES = ["partner", "manager", "staff", "reviewer"]
CLIENT_ROLES = ["client_uploader", "client_viewer"]

SA, FA, P, M, S, R, CU, CV = ROLE_CODES

# ---------------------------------------------------------------- 05 §3 matrix
# (row label, permission code, {role: value})
MATRIX_ROWS: list[tuple[str, str, dict[str, str]]] = [
    ("Manage users / roles", "users.manage", {FA: ALLOW}),
    ("Create client / engagement", "client.manage", {FA: ALLOW, P: ALLOW, M: ALLOW}),
    ("Assign team to engagement", "engagement.assign", {FA: ALLOW, P: ALLOW, M: ALLOW}),
    ("Upload workbook / TB / PDF", "file.upload", {P: ALLOW, M: ALLOW, S: ALLOW, CU: ALLOW}),
    ("Run checks", "run.check", {P: ALLOW, M: ALLOW, S: ALLOW, R: ALLOW}),
    ("View findings", "finding.view", {P: ALLOW, M: ALLOW, S: ALLOW, R: ALLOW, CU: SHARED_ONLY, CV: SHARED_ONLY}),
    ("Propose fix plan", "fix.propose", {P: ALLOW, M: ALLOW, S: ALLOW}),
    ("Approve and apply fix plan", "fix.approve", {P: ALLOW, M: NOT_OWN}),
    ("Edit narrative text (typed)", "narrative.edit", {P: ALLOW, M: ALLOW, S: MAKER_CHECKER}),
    ("Approve balancing adjustment", "balancing.approve", {P: ALLOW, M: MAKER_CHECKER}),
    ("Request AI review (payload preview)", "ai.request", {P: ALLOW, M: ALLOW, S: ALLOW}),
    ("Approve AI call", "ai.approve", {FA: POLICY, P: ALLOW, M: NOT_OWN}),
    ("Render draft PDF", "render.draft", {P: ALLOW, M: ALLOW, S: ALLOW}),
    ("Release draft to client", "release.client", {P: ALLOW, M: ALLOW}),
    ("Mark FINAL and lock engagement", "engagement.lock", {P: ALLOW}),
    ("Unlock a locked engagement", "engagement.unlock", {P: DUAL}),
    ("Download files", "file.download", {P: ALLOW, M: ALLOW, S: ALLOW, R: ALLOW, CU: ROLE_SETTING, CV: ROLE_SETTING}),
    ("View audit log", "audit.view", {FA: ALLOW, P: OWN_CLIENTS, M: OWN_CLIENTS, R: ALLOW}),
    ("Backup / restore", "backup.manage", {SA: ALLOW, FA: APPROVE_RESTORE}),
]

# Permissions the endpoints need beyond the 05 §3 rows (derived from 05 §2 / 06 §3 / 08).
EXTRA_PERMISSIONS: list[tuple[str, str, dict[str, str]]] = [
    ("View engagements", "engagement.view", {P: ALLOW, M: ALLOW, S: ALLOW, R: ALLOW, CU: ALLOW, CV: ALLOW}),
    ("Move engagement through its workflow", "engagement.transition", {P: ALLOW, M: ALLOW, S: ALLOW}),
    ("Co-approve unlock (dual control)", "engagement.unlock_coapprove", {FA: ALLOW}),
    ("Edit engagement configuration", "config.edit", {P: ALLOW, M: ALLOW, S: ALLOW}),
    ("View files and versions", "file.view", {P: ALLOW, M: ALLOW, S: ALLOW, R: ALLOW, CU: SHARED_ONLY, CV: SHARED_ONLY}),
    ("Update finding status / assignee", "finding.update", {P: ALLOW, M: ALLOW, S: ALLOW}),
    ("Raise review notes", "review.raise", {P: ALLOW, M: ALLOW, R: ALLOW}),
    ("Respond to review notes", "review.respond", {P: ALLOW, M: ALLOW, S: ALLOW, R: ALLOW}),
    ("Clear review notes", "review.clear", {P: ALLOW, R: ALLOW}),
    ("Disclosure checklist", "disclosure.edit", {P: ALLOW, M: ALLOW, S: ALLOW}),
    ("Client portal", "portal.view", {CU: ALLOW, CV: ALLOW}),
    ("Answer client queries", "portal.respond", {CU: ALLOW, CV: ALLOW}),
    ("Confirm management representations", "portal.confirm", {CV: ALLOW}),
    ("Export / import engagement packages", "engagement.export", {P: ALLOW, M: ALLOW}),
    ("Promote a quarantined engagement", "engagement.promote", {P: ALLOW}),
    ("View rule registry", "rules.view", {SA: ALLOW, FA: ALLOW, P: ALLOW, M: ALLOW, S: ALLOW, R: ALLOW}),
    ("Compliance administration (DPDP, CERT-In, access reviews, evidence)", "admin.compliance", {FA: ALLOW}),
    ("Operations (health, backups, releases, render workers)", "ops.manage", {SA: ALLOW, FA: ALLOW}),
    ("AI policy and settings", "ai.policy", {FA: ALLOW}),
]

ALL_PERMISSIONS: list[tuple[str, str, dict[str, str]]] = MATRIX_ROWS + EXTRA_PERMISSIONS
PERMISSION_CODES = [code for _, code, _ in ALL_PERMISSIONS]

# Action classes used by ABAC policies.
ACTION_CLASS: dict[str, str] = {
    "users.manage": "admin",
    "client.manage": "write",
    "engagement.assign": "admin",
    "file.upload": "write",
    "run.check": "write",
    "finding.view": "read",
    "fix.propose": "write",
    "fix.approve": "write",
    "narrative.edit": "write",
    "balancing.approve": "write",
    "ai.request": "write",
    "ai.approve": "write",
    "render.draft": "write",
    "release.client": "write",
    "engagement.lock": "write",
    "engagement.unlock": "unlock",
    "engagement.unlock_coapprove": "unlock",
    "file.download": "download",
    "audit.view": "read",
    "backup.manage": "admin",
    "engagement.view": "read",
    "engagement.transition": "write",
    "config.edit": "write",
    "file.view": "read",
    "finding.update": "write",
    "review.raise": "write",
    "review.respond": "write",
    "review.clear": "write",
    "disclosure.edit": "write",
    "portal.view": "read",
    "portal.respond": "write",
    "portal.confirm": "write",
    "engagement.export": "download",
    "engagement.promote": "admin",
    "rules.view": "read",
    "admin.compliance": "admin",
    "ops.manage": "admin",
    "ai.policy": "admin",
}


def default_value(role: str, permission: str) -> str:
    for _, code, cells in ALL_PERMISSIONS:
        if code == permission:
            return cells.get(role, DENY)
    return DENY


def strongest(values: list[str]) -> str:
    best = DENY
    for v in values:
        if STRENGTH.index(v) < STRENGTH.index(best):
            best = v
    return best
