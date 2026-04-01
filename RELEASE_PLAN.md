# RELEASE PLAN — gmail-mcp

## Background & Context

See these docs for the broader strategy and execution plan:
- `~/.openclaw/workspace/MARKET_HYPOTHESIS.md` — market segments (H1 builder / H2 institutional / H3 aspiring investor), guiding principle, strategic edge, GTM path
- `~/.openclaw/workspace/WEEKEND_SPRINT.md` — phased release plan (Phase 1–7), execution order, per-package checklists

---

**Package:** `gmail-mcp`
**Phase:** 1 (Quick Infra Win)
**Goal:** Release standalone MCP package for Gmail access via Claude.

## Tasks
- [ ] Ensure credentials/tokens are in `.gitignore` and not in git history
  - `gmail_credentials.json`, `gmail_token.pickle` must never be public
- [ ] Write README (currently missing):
  - What it does
  - How to install
  - How to configure (Gmail OAuth setup)
  - Example Claude usage
- [ ] Verify `pyproject.toml` is configured correctly for release
- [ ] Make GitHub repo public
- [ ] Tag `v0.1.0` + create GitHub Release with release notes
