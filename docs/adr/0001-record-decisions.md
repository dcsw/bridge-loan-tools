# ADR-0001: Record architecture decisions

Status: accepted

## Context
Decisions about tooling, versions and architecture need to be discoverable and reviewable.

## Decision
We record each significant decision as a numbered, immutable ADR in `docs/adr/`. Superseding a
decision means adding a new ADR that references the old one.

## Consequences
Decisions are traceable in git history; changes require a reviewed PR.
