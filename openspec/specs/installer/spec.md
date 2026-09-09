# Installer Specification

## Purpose

Windows installation of `ms-access-mcp`: uv setup, Python 3.11 tool install, doctor gating, registration controls.

## Requirements

### Requirement: uv Detection and Bootstrap
The installer MUST detect an existing uv before bootstrapping it.

#### Scenario: uv present vs missing
- GIVEN uv is on PATH
- WHEN the installer runs
- THEN no bootstrap occurs
- GIVEN uv is absent
- THEN uv is bootstrapped before tool installation

### Requirement: Doctor Gate
The installer MUST install the tool for Python 3.11+ and MUST abort before any registration when doctor fails.

#### Scenario: doctor failure blocks registration
- GIVEN doctor reports failure after install
- WHEN the installer proceeds
- THEN it aborts; no registration write occurs

### Requirement: WhatIf
The installer MUST support `-WhatIf` producing no mutations; controls SHOULD allow skipping registration.

#### Scenario: whatif is side-effect free
- WHEN the installer runs with `-WhatIf`
- THEN no file is created, modified, or deleted

### Requirement: No Secrets
The installer and configure flows MUST NOT write credentials or secrets to any configuration file.

#### Scenario: S9 — no secrets
- GIVEN any install or registration run
- WHEN configuration files are written
- THEN they contain no credentials, tokens, or secrets
