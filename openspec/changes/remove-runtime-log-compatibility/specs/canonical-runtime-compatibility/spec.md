## Purpose

Defines the canonical-only data boundary by making legacy runtime log formats inaccessible to the application while preserving existing files for separately authorized offline handling.

## ADDED Requirements

### Requirement: Runtime has no legacy log route

The application MUST NOT create, write, parse, or read legacy logs, traces, LLM JSONL, old session JSON, old-root runtime databases, or legacy tool-result directories.

#### Scenario: New isolated session

- **WHEN** a new session performs a one-shot run
- **THEN** only canonical storage, canonical-derived projections, and explicitly configured artifacts are created

### Requirement: Legacy CLI options are removed

The CLI MUST NOT expose or accept authority, rollback, or canonical-approval options whose purpose was to select a legacy route.

#### Scenario: Old authority flag is supplied

- **WHEN** a user supplies a removed legacy authority flag
- **THEN** argument parsing rejects it and does not start a legacy or shadow runtime

### Requirement: Existing legacy files are not mutated

Removing compatibility MUST NOT delete, rewrite, or migrate existing legacy files as a side effect of startup, listing, or resume.

#### Scenario: Legacy files are present

- **WHEN** old logs or sessions exist beside a canonical database
- **THEN** the application leaves their bytes unchanged and uses only canonical data
