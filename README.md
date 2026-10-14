# Schrift

Self-hosted notities, wiki en taken in Markdown. Volledig te bedienen door AI via een MCP-server, en taken synchroon met elke CalDAV-app, zoals Apple Herinneringen.

- **Markdown op schijf.** Elke notitie is een gewoon `.md`-bestand in een map. Leesbaar voor mensen, AI en andere tools.
- **Taken in je tekst.** Elke `- [ ]` in een notitie is een taak, met optioneel `📅 2026-10-20 14:00`. Afvinken op je telefoon zet `[x]` in de notitie.
- **AI-native.** Een MCP-server met 27 tools geeft Claude (of een andere assistent) dezelfde mogelijkheden als jij: lezen, zoeken, schrijven, taken beheren, gespreksverslagen maken en importeren. Elke wijziging is een versie die je kunt terugdraaien.
- **Rustige interface.** WYSIWYG-editor, mappenboom die je zelf ordent, licht en donker thema, werkt op je telefoon als app.

Het ontwerp en de keuzes staan in [docs/PLAN.md](docs/PLAN.md).

## Functies

| | |
|---|---|
| Notities | Mappen en submappen, tags, `[[wikilinks]]` met autocomplete, backlinks, versiegeschiedenis, prullenbak |
| Editor | WYSIWYG met `/`-menu, takenlijsten, datumkiezer, tabellen, uitklapblokken, plakken en slepen van afbeeldingen en bestanden |
| Taken | Overzicht per Verlopen, Vandaag, Komende 7 dagen, Later en Zonder datum. Filter per map. Snel toevoegen aan de Inbox |
| Zoeken | Full-text (titels, tekst, tags en tekst uit PDF's) plus semantisch zoeken met een lokaal model |
| CalDAV | Eén takenlijst per gebruiker voor Apple Herinneringen, Thunderbird, DAVx⁵ en andere |
| MCP | OAuth 2.1 voor claude.ai en de Claude-apps, Bearer-tokens voor Claude Code |
| Accounts | Meerdere gebruikers, wachtwoord plus verplichte tweestapsverificatie, app-wachtwoorden per apparaat |
| Taal | Nederlands en Engels |

## Zelf hosten

Schrift draait als één container met al zijn gegevens in `/data`. Zet hem achter een reverse proxy die HTTPS verzorgt (Caddy, Traefik, nginx…). HTTPS is verplicht, behalve op `localhost`.

```bash
git clone https://github.com/CasvanLeijden/Schrift.git && cd Schrift
export SCHRIFT_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
# Pas SCHRIFT_BASE_URL in docker-compose.yml aan naar je eigen adres.
docker compose up -d --build
docker compose exec schrift schrift user create jouwnaam --admin
```

Log daarna in op je adres. Bij de eerste keer stel je tweestapsverificatie in met een authenticator-app. Extra gebruikers maak je aan via **Instellingen › Gebruikers**.

### Kant-en-klaar image en automatisch bijwerken

Elke commit op `main` die door de tests komt, wordt gepubliceerd als `ghcr.io/casvanleijden/schrift:latest` (plus een tag `sha-…` per commit). Wil je niet zelf bouwen, gebruik dan dat image in plaats van `build: .`:

```yaml
services:
  schrift:
    image: ghcr.io/casvanleijden/schrift:latest
```

Automatisch bijwerken kan zonder dat GitHub toegang tot je server nodig heeft: laat de server periodiek het nieuwste image ophalen. `docker compose up -d` herstart de container alleen als het image is veranderd. Bijvoorbeeld met een systemd-timer:

```ini
# /etc/systemd/system/schrift-update.service
[Service]
Type=oneshot
WorkingDirectory=/opt/schrift
ExecStart=/usr/bin/docker compose pull -q
ExecStart=/usr/bin/docker compose up -d

# /etc/systemd/system/schrift-update.timer
[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
```

### Configuratie

| Variabele | Standaard | Betekenis |
|---|---|---|
| `SCHRIFT_BASE_URL` | `http://localhost:8000` | Publiek adres. Gebruikt voor links in taken en voor OAuth. |
| `SCHRIFT_SECRET_KEY` | — | Lange willekeurige tekst voor sessies. Verplicht in productie. |
| `SCHRIFT_DATA_DIR` | `/data` (container) | Notities, bijlagen, database en het zoekmodel. |
| `SCHRIFT_SEMANTIC_SEARCH` | `true` | Semantisch zoeken. Downloadt eenmalig een model van ~220 MB naar `/data/models`. |
| `SCHRIFT_MAX_UPLOAD_MB` | `25` | Maximale grootte van een bijlage. |
| `SCHRIFT_TRUSTED_ORIGINS` | `[]` | Extra origins (JSON-lijst) die formulieren mogen posten, als je Schrift onder meerdere adressen draait. |

### Reverse proxy

Stuur alles door naar poort 8000, inclusief de paden `/caldav`, `/.well-known/` en `/mcp`. Zet de headers `X-Forwarded-Proto` en `X-Forwarded-For`. Een voorbeeld voor Caddy:

```
schrift.example.com {
    reverse_proxy localhost:8000
}
```

### Backup

Alles staat in `/data`:

- `users/<id>/notes/` – de Markdown-bestanden (bron van de waarheid)
- `users/<id>/attachments/` – bijlagen
- `schrift.db` – index, taken, versies, accounts en tokens

Maak een consistente kopie van de database voordat je `/data` back-upt, bijvoorbeeld:

```bash
docker compose exec schrift python -c "import sqlite3; s=sqlite3.connect('/data/schrift.db'); d=sqlite3.connect('/data/schrift.backup.db'); s.backup(d)"
```

Bewerk je Markdown-bestanden buiten Schrift om (bijvoorbeeld na een restore of met een ander programma)? Dan neemt `schrift scan` de wijzigingen op. Dat gebeurt ook automatisch bij elke start.

## Taken synchroniseren (CalDAV)

1. Maak in **Instellingen › CalDAV** een app-wachtwoord voor je apparaat.
2. iPhone of iPad: **Instellingen › Apps › Herinneringen › Herinneringen-accounts › Voeg account toe › Andere › Voeg CalDAV-account toe**. Vul bij Server je domeinnaam in (`schrift.example.com`), plus je gebruikersnaam en het app-wachtwoord.
3. Andere apps: gebruik de server-URL `https://schrift.example.com/caldav/`.

Je taken verschijnen in de lijst **Schrift**. Taken uit een map krijgen de hoofdmap als voorvoegsel, bijvoorbeeld `[ZOKA] Offerte sturen`. In het notitieveld staat een link naar de notitie. Wat je in de app aanpast (afvinken, titel, datum, verwijderen), wordt teruggeschreven in de notitie. Nieuwe taken uit de app komen in je Inbox.

## Claude koppelen (MCP)

**claude.ai, Claude desktop en de mobiele apps:** voeg een custom connector toe met de URL `https://schrift.example.com/mcp`. Je logt in bij Schrift en geeft toestemming.

**Claude Code:** maak een token in **Instellingen › MCP-server** en voeg de server toe:

```bash
claude mcp add --transport http schrift https://schrift.example.com/mcp \
  --header "Authorization: Bearer sk_schrift_..."
```

De server heeft tools voor notities (zoeken, lezen, maken, gericht bewerken, verplaatsen, verwijderen, versies), mappen, tags, taken, bijlagen, gespreksverslagen (`create_meeting_report`) en bulkimport (`import_notes`). Daarnaast zijn er twee prompts: **gespreksverslag** en **importeren**.

## Ontwikkelen

Vereist: Python 3.12, [uv](https://docs.astral.sh/uv/) en Node 18 of nieuwer.

```bash
cp .env.example .env
make setup      # Python- en JS-pakketten, bundels en CSS
uv run schrift user create jij --admin
make dev        # http://localhost:8000
make js-watch   # editor-bundel opnieuw bouwen bij wijzigingen
make css-watch  # Tailwind opnieuw bouwen bij wijzigingen
make test       # pytest en de editor-roundtriptests
make lint
```

| Map | Inhoud |
|---|---|
| `schrift/services/vault.py` | Het enige schrijfpad voor notities, mappen en taken |
| `schrift/markdown.py` | Frontmatter, taken, wikilinks |
| `schrift/caldav.py` | CalDAV-server |
| `schrift/mcp_server.py`, `schrift/oauth.py` | MCP-tools en OAuth |
| `schrift/web/` | Web-routes en API |
| `frontend/src/` | Editor (TipTap) en de interactie in de sidebar |

## Licentie

[MIT](LICENSE).
