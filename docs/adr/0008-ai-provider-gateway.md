# ADR-0008: AI reviewer: off by default; Anthropic API or Amazon Bedrock (ap-south-1); approval token bound to the payload hash

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 02 §9, 05 §4, C9, D6, E-AI-01…05, Q5

## Context

Q5 (AI platform, region, retention). The AI reviewer is optional (02 §9, D6); client data must not leave the firm without explicit, per-payload approval; the model must never decide a number.

## Decision

**Provider `none` by default.** When the firm enables AI: Anthropic API or **Amazon Bedrock in ap-south-1 (Mumbai)**, chosen per the firm's data-residency and retention terms; the **region is a mandatory setting with no default**. Default model `claude-opus-5-5` through the official `anthropic` SDK with structured outputs and explicit refusal / max-tokens handling. Payloads are minimised and pseudonymised (names, CIN, PAN, DIN, membership numbers → stable tokens); the user sees the exact payload, size, destination and reason; a **different user** approves; the approval token is **bound to the payload SHA-256**, so any change invalidates it. Only the ai-gateway sends (ADR-0020). AI output is advisory and every number it cites is re-verified by the engine (E-AI-05).

## Consequences

- Good: Confidentiality preserved by construction; full audit of every call.
- Good: Provider-agnostic (local model provider also possible).
- Bad / cost: Approval friction is intentional; budgets and an evaluation set are needed before enabling (E-AI-02/04).
