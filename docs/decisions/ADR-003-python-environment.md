# ADR-003: Python environment and packaging

Status: Accepted
Date: 2026-11-22

## Context
Development happens on two Windows laptops (Intel Iris Xe; RTX 4070 Laptop). Python 3.12.1 is installed, which satisfies the brief's 3.11+ requirement. `uv` is not installed. The project needs reproducible setup for a new developer (brief §12 definition of done).

## Options
1. Standard-library `venv` + `pip`, with dependencies declared in `pyproject.toml`.
2. `uv`, with `pyproject.toml` and a lockfile.

## Decision
Use **`venv` + `pip`**. Dependencies are declared in `backend/pyproject.toml`, and dev tooling (pytest, httpx, ruff, type checker) goes in a `[dev]` optional-dependency group. Install with `pip install -e ".[dev]"`.

## Rationale
No additional tooling to install on either machine, and the setup is familiar to any reviewer. `pyproject.toml` keeps a later move to `uv` cheap if lockfile reproducibility becomes important.

## Consequences
- No lockfile by default. Specify minimum versions in `pyproject.toml`. For benchmark runs (M5), record exact package versions (e.g. `pip freeze`) alongside run metadata so results remain reproducible.
- The README documents Windows activation (`.venv\Scripts\activate`) alongside POSIX activation.

## Verification (planned)
- Fresh clone → create venv → `pip install -e ".[dev]"` → `pytest` passes without model access.
