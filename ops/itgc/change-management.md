# Change management (E-ITGC-01)

**Scope:** every production change — application releases, configuration (`ops/.env`, Caddyfile,
compose / Helm values), infrastructure (IaC), Excel build updates on render workers, rule-set and template
releases (also E-ITGC-02), and emergency fixes.

## 1. Standard change

1. **Ticket** (template: release-ticket-template.md): what, why, risk, affected controls, test evidence,
   rollback plan, planned window. Client data is never attached.
2. **Build and test:** merged through a pull request with review by someone other than the author
   (two-person review for `fscore` rules and security code, E-SEC-12); CI green: lint, type checks, unit and
   golden tests, RBAC matrix, Playwright smoke, pip-audit / npm audit, SAST, Trivy, SBOM.
3. **Approve:** approver ≠ developer. Approval recorded in the ticket and in the platform's `release` table.
4. **Deploy** a signed, tagged release in the window (outside critical periods, ops/bcp.md §1); take a
   pre-migration backup (`pgbackrest-backup.sh incr`) before Alembic migrations.
5. **Verify:** smoke test, `/api/v1/ready`, audit chain verification, error-budget panel; close the ticket
   with the release ID.

## 2. Emergency change

Allowed to restore service or close a critical vulnerability (critical SLA 7 days, high 30 days, E-SEC-08).
One approver may authorise verbally; the ticket is completed and **reviewed within 48 hours** by a second
approver. Emergency changes are listed separately in the monthly operations report.

## 3. Release log reconciliation

Monthly: the list of deployments (container image digests running, Helm history / compose build logs) is
reconciled 1:1 with the `release` table. Any deployment without a ticket is an exception for the operations
report and the evidence pack.

## 4. Error budgets

While `ErrorBudgetBurn` fires or the 30-day availability is below 99.5 %, only fixes and emergency changes
ship (E-OPS-01).

## 5. Rollback

Every ticket names the rollback: previous image digest (`FS_VERSION` / Helm revision), database restore
point if a migration is not backward-compatible, and configuration file version. Rolling back is itself a
change recorded in the ticket.
