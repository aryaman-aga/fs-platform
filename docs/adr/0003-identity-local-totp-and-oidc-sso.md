# ADR-0003: Identity: local accounts with mandatory TOTP, OIDC SSO for firm users

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 05 §1, E-SEC-01, E-SEC-03, Q2, Q13

## Context

Q2 (users, SSO) and Q13 (identity provider, SCIM). 05 §1 makes MFA mandatory for everyone; 12 E-SEC-01 makes SSO mandatory for firm users at T1 and SCIM a T2 item. Client users do not have the firm's IdP.

## Decision

Firm users sign in through **OIDC SSO with Microsoft Entra ID** (Google Workspace supported by the same OIDC client) when configured; local accounts exist for **client users and sealed break-glass** only. Every local login requires **TOTP** (recovery codes shown once). Passwords: argon2id, ≥ 12 characters, offline breached-password list, lockout 5 failures / 15 minutes, rate limits instead of CAPTCHA. Sessions are DB-backed (stateless API nodes) with 30/60-minute idle and 12-hour absolute limits. **WebAuthn and SCIM 2.0 provisioning are T2.** Admin actions need step-up MFA and a reason; just-in-time elevation and break-glass are audited and alerted.

**Desktop edition (1.0.1 and later):** one PC, served on 127.0.0.1 only. The authenticator is optional there and off by default (`[app] mfa_required` in the data folder's `fsplatform.ini`); the password signs in and confirms privileged actions, and people may create their own accounts, which see nothing until an administrator assigns a role. The firm server refuses to start with the authenticator off or self-service accounts on. Risk and mitigations: threat model T52.

## Consequences

- Good: Leavers lose access centrally in the IdP (E-ITGC-03).
- Good: No dependency on a third-party IdP for client users.
- Bad / cost: Two login paths to test and support.
- Bad / cost: Until SCIM (T2), de-provisioning in the IdP must be mirrored by disabling the platform user (procedure in ops/itgc/access-management.md).
