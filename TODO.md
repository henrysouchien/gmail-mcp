# TODO

## Release & Packaging
See `RELEASE_PLAN.md` in the repo root for the full packaging and release plan for this repo.

## Security: Migrate to venv isolation

> Added: 2026-03-24 (supply chain audit)

Move this project from global pip to a dedicated virtual environment. Reduces blast radius if any dependency is compromised — a single malicious package can only access deps in the same venv, not all 500+ global packages.

- [ ] Create `.venv` in project root (`python3 -m venv .venv`)
- [ ] Install project deps into venv (`pip install -e .` or `pip install -r requirements.txt`)
- [ ] Verify project runs correctly from venv
- [ ] Add `.venv/` to `.gitignore` if not already present
- [ ] Remove global editable install (`pip uninstall <package>` from global)
