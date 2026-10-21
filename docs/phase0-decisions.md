# Phase-0 questions and the answers used for the build

The build pack asks for the questions in 11 §2 and 12 §7 to be put to the firm in phase 0. The user asked
for an autonomous end-to-end build, so each question was answered with the recommended option and
recorded as an ADR. Items marked **confirm with the firm** must be ratified (or changed) by the partners
before go-live; every answer is a configuration or data change unless stated otherwise.

| # | Question | Answer used | ADR | Confirm with the firm |
|---|---|---|---|---|
| 1 | Hosting: office server, India cloud or hybrid? Who administers it? | Office Linux server, single-node Docker Compose; India-region cloud (Azure Central India) as alternative and DR target; administered by the firm's IT lead | 0002 | yes — hardware, administrator |
| 2 | Users: how many firm and client users? SSO? | Sized for 50 concurrent (HA profile: 200); SSO via OIDC for firm users, local accounts + TOTP for client users | 0002, 0003 | yes — user counts |
| 3 | Mode B (Excel on the server) at go-live? | No — deferred to T2 with E-SEC-09 hardening; Excel used only by the render pool | 0004 | yes |
| 4 | Domain and remote access: Cloudflare Tunnel or VPN? | `fs.<firm-domain>` (placeholder `fs.example-firm.in`) through Cloudflare Tunnel; WireGuard VPN as fallback | 0002 | yes — domain |
| 5 | AI: platform, region, retention terms? | Off by default; when enabled Anthropic API or Amazon Bedrock ap-south-1, region mandatory, model `claude-opus-5-5`, zero-retention terms where contracted | 0008 | yes — platform, contract |
| 6 | Retention: 7 years or longer? Legal hold? | 7 years from the report date (configurable, never lower), legal hold supported | 0009 | yes |
| 7 | House style for Module P | Built-in pack v1 (Big Four-style Schedule III); firm's signed sample becomes pack v2 | 0010 | yes — provide sample set |
| 8 | Clients' TB formats; mapping ownership | NetSuite saved-search layout + generic CSV/XLSX; mappings owned by the engagement Manager, versioned | 0011 | yes |
| 9 | Backups: NAS, off-site target, key holder | NAS on a separate VLAN + India-region object storage with object lock; keys split 2-of-3 among partners | 0012 | yes — NAS model, bucket |
| 10 | Billing basis; invoices in or outside the app | Per client per month usage; invoicing outside the app | 0013 | yes |
| 11 | ISO 27001 certification, SOC 2 report or aligned? | ISO 27001-aligned, SOC 2-ready evidence; no certification in year 1 | 0014 | yes |
| 12 | HA from day one or single node first? | Single node first; HA profile ready (Helm) for phase 9 / growth | 0002 | yes |
| 13 | IdP: Entra ID or Google; SCIM on the plan? | Microsoft Entra ID via OIDC (Google supported); SCIM in T2 | 0003 | yes — tenant, SCIM licence |
| 14 | CERT-In point of contact; DPDP grievance contact | **To be named by the firm**; both are configurable in *Admin → Compliance settings*; until named, the Firm Admin acts for both | — | **yes — required before go-live** |
| 15 | ERPs used most (connector priority) | NetSuite, then Tally, SAP, Zoho (adapters T2) | 0011 | yes |
| 16 | Consolidation in year 1? | No — T2 (E-PREP-03) | 0015 | yes |
| 17 | Frameworks beyond Ind AS Div II in year 1? | None; Division I/III, IFRS, US GAAP packs in T2 | 0015 | yes |
| 18 | Signing: DSC tokens or Aadhaar eSign? | Partners' Class 3 DSC tokens outside the platform; UDIN captured | 0016 | yes |
| 19 | Three key holders for Shamir escrow | Three partners (names recorded offline, not in the repository) | 0012 | **yes — name them** |
| 20 | Budget for a second site / cloud DR | Year 1: off-site backups + IaC rebuild (RTO 4 h); warm DR site T2 | 0017 | yes |

## Other phase-0 outputs

- Threat model v1 (STRIDE): `docs/security/threat-model.md`
- Initial control matrix: `docs/control-matrix-draft.md` (code/test links are completed in `docs/CONTROL_MATRIX.md`)
- Repository skeleton with CI, linting and tests: `.github/workflows/ci.yml`
- Accounting-presentation choices: ADR-0018
