# Schrift

Self-hosted Markdown notes, wiki and tasks with a CalDAV server and an MCP server.
The plan, decisions and design tokens are in `docs/PLAN.md` (Dutch). Read it before larger changes.

## Commands

- `make setup` installs Python deps (uv) and builds the CSS
- `make dev` runs the app with reload on http://localhost:8000
- `make css-watch` rebuilds Tailwind on template changes
- `make test`, `make lint`, `make format`

## Conventions

- Python 3.12, FastAPI, SQLite, Jinja2 + HTMX + Alpine.js, Tailwind v4 (standalone CLI via `scripts/tailwind.sh`)
- Markdown files on disk are the source of truth for note content; SQLite is the index
- Every write (web, MCP, CalDAV) goes through one service layer so versions, tasks and the index stay in sync
- UI copy in Dutch and English; no modals for everyday actions; autosave
- Colors come from the CSS tokens in `schrift/static/css/app.css`; never hard-code hex values in templates
- The repo is deployment-independent: no hostnames, servers or infrastructure details of a specific install
- Commits are small and focused, in English
