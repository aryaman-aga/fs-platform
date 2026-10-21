# ADR-0019: Runtimes: Python 3.11+ (tested on 3.13), Node LTS, React + TypeScript + Vite

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** D2, E-SEC-12

## Context

D2 fixes Python + FastAPI + PostgreSQL + React/TypeScript. Versions must be supported for the life of a release.

## Decision

Python **3.11+**, CI on 3.11 and 3.13; **Node.js 20+** for building the UI with an **active LTS** used in CI (Node 20 reached end of life in April 2026; CI uses 22 LTS); **React + TypeScript + Vite**, TanStack Query, PDF.js. Dependencies pinned with hashes for production builds; SBOM per release.

## Consequences

- Good: Supported runtimes with security fixes.
- Bad / cost: Yearly runtime upgrades through change control.
