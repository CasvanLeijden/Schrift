# Schrift

Self-hosted notities, wiki en taken in Markdown. Volledig te bedienen door AI via een MCP-server, en taken synchroon met elke CalDAV-app (zoals Apple Herinneringen).

> Status: in ontwikkeling. Zie [docs/PLAN.md](docs/PLAN.md) voor het plan en [docs/design/mockup](docs/design/mockup) voor het ontwerp.

## Wat het doet

- Notities als echte `.md`-bestanden in mappen, met tags, `[[wikilinks]]`, backlinks en versiegeschiedenis
- WYSIWYG-editor in de browser, ook op mobiel
- Elke `- [ ]` in een notitie is een taak, met optionele vervaldatum
- CalDAV-server voor taken: afvinken op je telefoon werkt door in de notitie
- MCP-server (OAuth of Bearer-token) zodat AI-assistenten notities en taken kunnen lezen en bewerken
- Full-text en semantisch zoeken, lokaal
- Meerdere gebruikers, 2FA, app-wachtwoorden

## Stack

Python 3.12, FastAPI, SQLite, Jinja2 + HTMX + Alpine.js, Tailwind CSS, TipTap.

## Ontwikkelen

Vereist: Python 3.12 en [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
make setup
make dev        # http://localhost:8000
make test
```

## Zelf hosten

```bash
export SCHRIFT_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
docker compose up -d
```

Alle gegevens (notities, bijlagen, database) staan in het volume `/data`. Zet Schrift achter een reverse proxy met HTTPS.
