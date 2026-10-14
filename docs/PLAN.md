# Schrift: plan

Self-hosted notitie-, wiki- en takenapp. Markdown als bron, volledig te bedienen door Claude via MCP, en taken synchroon met Apple Herinneringen via CalDAV.

Schrift is deployment-onafhankelijk: één Docker-image met een `/data`-volume, te draaien achter elke reverse proxy met HTTPS. In deze documentatie staat `schrift.example.com` als voorbeelddomein.

---

## 1. Uitgangspunten (uit de vragenronde)

| Onderwerp | Keuze |
|---|---|
| Gebruikers | Ik + een paar anderen, admin maakt accounts aan. Volledig gescheiden data; delen komt later. |
| Organisatie | Mappen (Prive, Forfacts, ZOKA, met submappen) + vrije tags. Tags staan bovenaan de notitie (frontmatter) en tonen als labels onder de titel. De hoofdmap is ook de context-tag. |
| Opslag | Echte `.md`-bestanden op schijf + SQLite als index (zoeken, taken, metadata, versies). |
| Taken | Elke `- [ ]` in een notitie is een to-do. Losse taken kunnen ook (Inbox, geen map). |
| Taken van anderen | Gewone bullet (`- Jan: offerte sturen`), dus geen to-do. |
| Taakvelden | Titel, status, optioneel een vervaldatum (+ optioneel tijd). Taken zonder datum zijn gewoon mogelijk. |
| Datumnotatie | `📅 2026-10-20 14:00` in Markdown, in de editor als klikbaar datumpilletje. |
| Afvinken | `[ ]` wordt `[x]`, regel blijft staan. |
| Herinneringen | Eén lijst "Schrift" per gebruiker. Map als prefix in titel: `[ZOKA] Offerte sturen`. Notitieveld bevat link naar de notitie. |
| Wijzigingen uit Herinneringen | Alles terugschrijven: titel, datum, status. Verwijderen in Herinneringen verwijdert de regel uit de notitie (wel in versiegeschiedenis). |
| Nieuwe taak uit Herinneringen | Gaat naar Inbox (geen map). |
| Afgeronde taken in de app | Standaard verborgen, filter "toon afgerond". |
| Wiki | `[[Wikilinks]]` met autocomplete, backlinks, versiegeschiedenis. |
| Gespreksverslagen | Via Claude/MCP, vaste structuur, map `<Context>/Verslagen`, ruwe aantekeningen bewaard. |
| Editor | WYSIWYG zoals Notion (`/`-menu, Markdown-shortcuts). |
| Bestanden | Slepen/plakken in editor, PDF's en andere bijlagen, PDF-tekst doorzoekbaar. |
| Zoeken | Full-text (FTS5) + semantisch (lokaal embeddingmodel). |
| MCP | Alles wat de gebruiker kan. Elke wijziging is een versie, verwijderen gaat naar de prullenbak. Werkt in claude.ai, desktop, iOS en Claude Code. |
| UI | Sidebar + editor, licht/donker volgens systeem, volwaardig mobiel (PWA), NL + EN. |
| Beveiliging | Gebruikersnaam + wachtwoord + TOTP (2FA). App-wachtwoorden voor CalDAV. MCP via OAuth (claude.ai) of via `Authorization: Bearer <token>`-header (Claude Code). |
| Import | Via MCP: Claude leest een bron en maakt notities aan. |
| Backup | Buiten deze repo. De app zet alles in `/data` en documenteert hoe je een consistente backup maakt; de backup-tooling zelf hoort bij de deployment. |
| Repo | Publiek op GitHub, naam **Schrift**. |

---

## 2. Stack

| Laag | Keuze | Waarom |
|---|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn | Gevraagd. |
| Database | SQLite (WAL-modus), SQLAlchemy 2 + Alembic | Gevraagd. Migraties via Alembic. |
| Zoeken | SQLite FTS5 | Ingebouwd, snel, geen extra dienst. |
| Semantisch zoeken | `fastembed` + `multilingual-e5-small` (~120 MB, NL+EN), vectoren in `sqlite-vec` | Lokaal: geen data naar buiten, geen kosten. |
| Templates | Jinja2 + HTMX + Alpine.js | Server-rendered, weinig JavaScript, past bij "simpel". |
| Styling | Tailwind CSS (standalone CLI, geen Node nodig in productie) | Gevraagd. |
| Editor | TipTap (ProseMirror) + `tiptap-markdown`, gebundeld met esbuild | Notion-achtige WYSIWYG met betrouwbare Markdown in/uit. Eigen extensies voor datumpilletjes, `[[links]]` en bijlagen. |
| CalDAV | Eigen minimale CalDAV-implementatie (alleen VTODO) in FastAPI, met `icalendar` | Volledige controle over de koppeling met Markdown-taken. Radicale gebruikt eigen opslag en past daar slecht bij. |
| MCP | Officiële Python MCP SDK, Streamable HTTP op `/mcp` | Werkt met claude.ai-connectors en Claude Code. |
| MCP-auth | OAuth 2.1 (PKCE + dynamic client registration) én persoonlijke API-tokens via `Authorization: Bearer` | claude.ai-connectors vereisen OAuth (login met wachtwoord + 2FA). Voor Claude Code is een token in de header het eenvoudigst. |
| Auth | argon2 wachtwoord-hashes, `pyotp` voor TOTP, sessie-cookies | |
| PDF-tekst | `pypdf` | Lichtgewicht. |
| i18n | Eenvoudige NL/EN-vertaalbestanden (Babel-catalogi) | |
| Deploy | Eén Docker-image, volume `/data` | |
| Tests | pytest, plus een CalDAV-testsuite op basis van echte Apple-verzoeken | |

---

## 3. Datamodel

### Op schijf (bron van de waarheid voor inhoud)

```
/data/
  schrift.db                         # SQLite: index, taken, versies, users
  users/<user>/notes/
    Prive/Boodschappen.md
    ZOKA/Klant X.md
    ZOKA/Verslagen/2026-10-14 Overleg Klant X.md
  users/<user>/attachments/<hash>/<bestandsnaam>
  users/<user>/trash/
```

Een notitie:

```markdown
---
id: 01JA8K...            # vaste ID, overleeft hernoemen/verplaatsen
tags: [offerte, klant-x]
created: 2026-10-14T09:12:00+02:00
updated: 2026-10-14T10:30:00+02:00
---
# Overleg Klant X

- [ ] Offerte sturen 📅 2026-10-20 14:00 🆔 k3f9x
- [ ] Klant X terugbellen 🆔 r8d2w
- [x] Agenda delen ✅ 2026-10-14 🆔 p2m7q
- Jan: contract nalezen

Zie ook [[Klant X]]. ![schets](attachments/ab12/schets.png)
```

- **`🆔`** is een korte, stabiele taak-ID. Die is nodig zodat CalDAV (UID), Claude en de app dezelfde taak blijven herkennen als de tekst verandert. De editor verbergt hem, in de ruwe Markdown is hij zichtbaar. Dit is hetzelfde formaat als de Obsidian Tasks-plugin, dus het is AI-leesbaar en draagbaar.
- `📅` is optioneel: een taak zonder datum is gewoon `- [ ] tekst`.
- Tags staan in de frontmatter (`tags:`) bovenaan de notitie; de editor toont ze als labels onder de titel.
- `✅ datum` wordt automatisch toegevoegd bij afvinken.
- Losse taken (Inbox) staan in een notitie `Inbox.md` per gebruiker, zodat ook die gewoon Markdown blijven en Claude ze kan lezen.

### SQLite (index en metadata)

`users`, `app_passwords`, `oauth_clients/tokens`, `folders`, `notes` (pad, titel, hash), `notes_fts`, `note_embeddings`, `tags`, `note_tags`, `links` (voor backlinks), `tasks` (id, note_id, regel, titel, due, done, etag), `note_versions` (volledige snapshot + bron: web/mcp/caldav/import), `attachments` (+ geëxtraheerde PDF-tekst).

Bij elke schrijfactie (web, MCP, CalDAV) gaat het via één service-laag: Markdown schrijven, versie opslaan, taken en links opnieuw parsen, index bijwerken. Zo kunnen de drie ingangen niet uit elkaar lopen.

---

## 4. Onderdelen

### 4.1 Web-UI
- **Sidebar**: zoeken (Cmd/Ctrl+K), Taken, Inbox, mappenboom, tags. Op mobiel inklapbaar.
- **Mappenboom**: toont mappen én alle notities erin (altijd alles, geen "meer tonen"). Mappen zijn in- en uitklapbaar (onthouden per gebruiker). Volgorde van mappen en notities pas je zelf aan door te slepen; nieuwe notities komen bovenaan. De volgorde staat in SQLite (`position`), niet in de bestandsnamen, zodat de Markdown-bestanden schoon blijven.
- **Editor**: WYSIWYG, autosave (geen opslaanknop), `/`-menu, `[[` voor links, plakken/slepen van bestanden, datumpilletje via `📅` of `/datum`.
- **Takenoverzicht**: gegroepeerd op Verlopen / Vandaag / Komende 7 dagen / Zonder datum. Filterknoppen per hoofdmap + Inbox, schakelaar "Toon afgerond". Bovenaan een invoerveld: typen + Enter = nieuwe taak in Inbox. Elke taak toont de bron (map › notitie) als link; afvinken in de lijst past de notitie aan.
- **Notitiepaneel** (inklapbaar, geen dialoog): tags, backlinks, bijlagen, versies.
- **Instellingen** als één pagina zonder pop-ups, met secties: Algemeen (taal, thema), Beveiliging (wachtwoord, 2FA), CalDAV (server-URL, app-wachtwoorden per apparaat; generiek, Apple Herinneringen is een voorbeeld), MCP-server (server-URL voor OAuth-clients, tokens voor header-auth, gekoppelde clients), Gebruikers (alleen admin). Een nieuw token/app-wachtwoord wordt eenmalig inline getoond.
- **Versie-indicator** in de kopbalk van een notitie: "Opgeslagen" en "Gewijzigd door Claude om 10:30", klik opent de versies.
- **Mobiel**: tabbalk onderaan (Notities, Taken, Zoeken, Nieuw) en een opmaakbalk boven het toetsenbord (kop, vet, taak, datum, `[[ ]]`, bijlage).
- Bevestigingen inline (bv. "Verwijderd · Ongedaan maken"-balk) in plaats van pop-ups.

### 4.2 Taken en CalDAV
- Discovery via `/.well-known/caldav`. Eén takenlijst "Schrift" per gebruiker.
- Ondersteunt wat Apple Herinneringen gebruikt: `PROPFIND`, `REPORT` (calendar-query, calendar-multiget, sync-collection), `GET`, `PUT`, `DELETE`, ETags en ctag/sync-token.
- Naar Herinneringen: titel `[ZOKA] Offerte sturen`, `DUE`, `STATUS`, `URL`/notitieveld met link naar de notitie.
- Vanuit Herinneringen: afvinken, titel of datum aanpassen of verwijderen wordt teruggeschreven in de Markdown-regel (de prefix wordt weer gestript). Nieuwe taak komt in Inbox.
- Inloggen in Herinneringen met gebruikersnaam + app-wachtwoord.
- Sync-snelheid: zoals iOS het doet (bij openen en periodiek).

### 4.3 MCP-server (`/mcp`)
Tools (allemaal per ingelogde gebruiker):

- **Notities**: `search` (full-text + semantisch, met filters op map/tag), `get_note`, `create_note`, `update_note` (volledig, of gericht: sectie vervangen/toevoegen), `move_note`, `delete_note` (prullenbak), `get_backlinks`, `list_versions`, `restore_version`
- **Structuur**: `list_folders`, `create_folder`, `rename_folder`, `list_tags`
- **Taken**: `list_tasks` (filters: open/afgerond, map, datum), `create_task` (in notitie of Inbox), `update_task`, `complete_task`
- **Bestanden**: `upload_attachment`, `get_attachment` (inclusief PDF-tekst)
- **Verslagen**: `create_meeting_report`, plus een MCP-prompt "Gespreksverslag" die Claude de werkwijze geeft
- **Import**: `import_notes` (bulk, meerdere notities in één aanroep) + een MCP-prompt met importinstructies, zodat Claude elk bronsysteem kan omzetten

Elke MCP-wijziging krijgt in de versiegeschiedenis het label "Claude", zodat je ziet wat AI heeft aangepast en het kunt terugdraaien.

### 4.4 Gespreksverslag-structuur
Pad: `<Context>/Verslagen/YYYY-MM-DD <Onderwerp>.md`

```markdown
# Overleg Klant X: planning Q4
**Datum:** 2026-10-14 · **Deelnemers:** Cas, Jan (Klant X) · **Context:** [[Klant X]]

## Samenvatting
## Besproken
## Besluiten
## Actiepunten
- [ ] Offerte sturen 📅 2026-10-20     ← mijn taken: to-do + Herinneringen
- Jan: contract nalezen                 ← anderen: gewone bullet
## Vervolg

<details><summary>Ruwe aantekeningen</summary>
...je originele notities...
</details>
```

### 4.5 Beveiliging
- Wachtwoord + verplichte TOTP voor web en OAuth (MCP).
- Persoonlijke API-tokens voor MCP via `Authorization: Bearer`, per token een naam, intrekbaar, aan te maken in Instellingen. Voorbeeld voor Claude Code: `claude mcp add --transport http schrift https://schrift.example.com/mcp --header "Authorization: Bearer <token>"`.
- App-wachtwoorden per apparaat voor CalDAV, intrekbaar.
- Rate limiting op login, veilige cookies, CSRF-bescherming voor formulieren/HTMX.
- Bijlagen alleen via geauthenticeerde URL's.

### 4.6 Deploy
- `Dockerfile` + voorbeeld-`docker-compose.yml`, volume `/data`, één HTTP-poort voor achter een reverse proxy.
- Configuratie via environment variables (`SCHRIFT_BASE_URL`, `SCHRIFT_SECRET_KEY`, …).
- De repo bevat geen omgevingsspecifieke gegevens; hoe en waar Schrift draait hoort bij de deployment, niet hier.
- Backup: de app zet alles in `/data`; de README beschrijft hoe je een consistente SQLite-backup maakt. Backup-tooling zelf valt buiten de repo.

---

## 5. UI/UX-aanpak

Ik gebruik Anthropic's officiële **frontend-design**-skill als leidraad. Die staat niet in deze repo (licentie), installeer hem lokaal met `/plugin marketplace add anthropics/claude-code` en `/plugin install frontend-design@claude-code-plugins`. Daarbovenop gelden deze eigen regels:

- Rustig en typografisch: veel witruimte, één accentkleur, leesbreedte max ~70 tekens in de editor.
- Geen modals voor gewone handelingen. Inline bewerken, een zijpaneel en undo-balkjes.
- Alles autosave.
- Toetsenbord-first op desktop (Cmd+K, `/`), touch-vriendelijke targets (≥44px) op mobiel.
- Toegankelijk: goede contrasten in licht en donker, focus-states, semantische HTML.

Alternatief dat ik heb bekeken: `ui-ux-pro-max` (community, erg populair, maar draait eigen scripts). Ik raad de officiële skill aan.

---

## 5b. Ontwerp (goedgekeurd in de mockup)

Mockup: https://claude.ai/artifact/TDfuBS7fKUaAPsqDfnwBmj (bronbestanden in `docs/design/mockup/`).

- **Richting**: een knipoog naar het Nederlandse schoolschrift. Een dunne rode kantlijn links van de notitietekst is het enige opvallende element; de rest is rustig.
- **Kleuren** (licht / donker):

| Token | Licht | Donker | Gebruik |
|---|---|---|---|
| `bg` | `#FFFFFF` | `#16191F` | achtergrond editor |
| `side` | `#F4F6F9` | `#1C2028` | sidebar |
| `line` | `#E2E6EC` | `#2C323C` | randen |
| `text` | `#19202B` | `#E5E8EE` | tekst |
| `muted` | `#5A6473` | `#A2ABB8` | secundaire tekst |
| `accent` | `#2443B0` | `#9DB1FF` | koningsblauw "inkt": links, selectie, datum |
| `accent-soft` | `#E8ECFA` | `#252D48` | achtergrond datumpil, actief item |
| `margin` | `#E0605A` | `#A9504B` | kantlijn |
| `late` | `#B3261E` | `#FF9B92` | verlopen taken |

- **Typografie**: Literata voor notitietekst en titels (leesletter, 17px / 1.7), Instrument Sans voor de interface. Leesbreedte max ~760px.
- **Taken in de editor**: echte checkbox + tekst + datumpil (kalendericoon, bv. "ma 20 okt, 14:00"). Afgevinkt = gevuld blauw vinkje, tekst gedempt (niet doorgestreept).
- **Iconen**: dunne lijniconen (inline SVG), geen emoji in de UI.

## 6. Fases

Elke fase eindigt met iets werkends dat je kunt proberen, en wordt in kleine commits gepusht.

0. **Setup**: UI-mockup afstemmen, GitHub-repo `Schrift`, `gh` installeren, skill installeren, projectskelet, Docker, CI (lint + tests).
1. **Basis**: login + 2FA, admin/gebruikers, mappen, notities als `.md`, editor met autosave, tags, bijlagen, full-text zoeken, licht/donker, NL/EN.
2. **Taken**: parser voor `- [ ]` + `📅`/`🆔`, Inbox, takenoverzicht, datumpilletje in editor.
3. **CalDAV**: Apple Herinneringen-sync, app-wachtwoorden. Testen met je iPhone.
4. **MCP**: OAuth, alle tools, verslag- en importprompts. Koppelen in claude.ai en Claude Code.
5. **Wiki en extra's**: `[[links]]` + backlinks, versiegeschiedenis-UI, PDF-tekst, semantisch zoeken, PWA.
6. **Livegang**: eerste productie-deploy, hardening, README + documentatie voor zelf hosten.

---

## 7. Besluiten na review

- Naam van app en repo: **Schrift**.
- Taak-ID `🆔` in de Markdown: akkoord.
- Tags: bovenaan de notitie (frontmatter).
- Taken zonder datum: ja.
- MCP ook via `Authorization: Bearer`-header.
- Backup: buiten de repo, later apart.
- De repo is deployment-onafhankelijk: geen domeinen, servers of infrastructuurdetails van een specifieke installatie.
- Mockup goedgekeurd; mappenboom toont altijd alle notities, in-/uitklapbaar, volgorde zelf te slepen.
- Instellingen generiek: secties "CalDAV" en "MCP-server" in plaats van "Apple Herinneringen" en "Claude".
- Notities kunnen naar een andere map gesleept worden; het bestand verhuist mee, de vaste ID houdt links en taken intact.
