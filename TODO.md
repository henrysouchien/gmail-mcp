# TODO

## Release & Packaging
See `RELEASE_PLAN.md` in the repo root for the full packaging and release plan for this repo.

## Security: Migrate to venv isolation

> Added: 2026-03-24 (supply chain audit)

Move this project from global pip to a dedicated virtual environment. Reduces blast radius if any dependency is compromised — a single malicious package can only access deps in the same venv, not all 500+ global packages.

- [x] Create dedicated `venv/` in project root (matches the repository README and existing ignore convention)
- [x] Install project deps into venv (`pip install -e .`)
- [x] Verify project runs correctly from venv
- [x] Keep `venv/` ignored (already present in `.gitignore`)
- [ ] Remove global editable install (`pip uninstall <package>` from global)

**Progress 2026-07-15:** the isolated environment is installed, the full suite
passes inside it (11 tests + 2 subtests), all 11 MCP tools register, and the
Claude MCP launcher now invokes `gmail-mcp/venv/bin/python` instead of global
`python3`. The personal-agents consumer now has its own complete venv and its
schedule installer renders that interpreter by default. Global uninstall is
deferred only until the six already-running global-interpreter Gmail MCP
processes retire naturally; removing package files underneath them is avoided.
