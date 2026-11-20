# Introduction 
TODO: Give a short introduction of your project. Let this section explain the objectives or the motivation behind this project. 

# Getting Started
TODO: Guide users through getting your code up and running on their own system. In this section you can talk about:
1.	Installation process
2.	Software dependencies
3.	Latest releases
4.	API references

# Build and Test
TODO: Describe and show how to build your code and run the tests. 

# Contribute
TODO: Explain how other users and developers can contribute to make your code better. 

If you want to learn more about creating good readme files then refer the following [guidelines](https://docs.microsoft.com/en-us/azure/devops/repos/git/create-a-readme?view=azure-devops). You can also seek inspiration from the below readme files:
- [ASP.NET Core](https://github.com/aspnet/Home)
- [Visual Studio Code](https://github.com/Microsoft/vscode)
- [Chakra Core](https://github.com/Microsoft/ChakraCore)

# Project structure (consolidated)
```
app/              single FastAPI entrypoint (serves /api/*, /retrieve, /health and built UI at /)
backend/          KB pipeline (ingestion, structuring, curation, store) - run from inside backend/
retrieval_agent/  similar-ticket retrieval (hybrid BM25 + vectors + LLM rerank)
ui/               React UI (Vite); ui/server_legacy_express = old Node API, replaced by app/routers/tickets.py
db/               schema.sql (vector(1536)), create_new_db.py, migrations/
data/raw          merged source JSON; data/archive/kavita = original Kavita inputs (xlsx, json, 512-dim schema)
tests/            pytest suite
```

# Run
```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp sample.env .env            # then fill values (a populated .env is git-ignored)
python db/create_new_db.py --dry-run   # shows names; drop --dry-run to create <db>_1536 (existing DB untouched)
# point PG_DATABASE / AZURE_PG_DATABASE in .env at the new DB, then:
cd backend && python step3_populate_data.py && python step4_store_embeddings.py && cd ..
psql -f db/migrations/002_vector_indexes.sql      # optional HNSW indexes, after embeddings exist
cd ui && npm ci && npm run build && cd ..
uvicorn app.main:app --port 8000                  # UI at http://localhost:8000/
pytest tests && python -m unittest retrieval_agent.tests
```
Azure App Service (Linux, Python 3.11): startup command `bash startup.sh`; set `.env` keys as app settings; deploy repo with built `ui/dist`.

# Similar historical tickets
`POST /api/tickets/similar` `{id?, title?, description?, module?, severity?}` runs the retrieval agent (hybrid BM25 + vector + LLM rerank) and returns ranked matches, each enriched with the matched ticket's description, system, resolution and RCA, plus `no_confident_match` and `suggested_next_step`. A ticket never matches its own ID.
In the UI the "Similar historical tickets" panel loads automatically in the ticket detail view. It searches the single ticket store, `ticket_records` (approved tickets whose embeddings match `EMBEDDING_DIMENSIONS`, default 3072, created by `backend/step3` + `step4`). For `npm run dev`, run `uvicorn app.main:app --port 8000` (the Vite proxy targets it).

# osTicket integration (source of truth for tickets)
osTicket is the ITSM system where tickets are created and worked. This app is the **AI Insights** layer on top: tagging, similar tickets, SOPs and curation. Users create tickets in osTicket (the UI's "New Ticket" button opens osTicket's `/open.php`); each ticket row in the UI links back to its osTicket page.

**How it works.** `app/services/osticket.py` reads tickets from osTicket and upserts them into `ticket_records` (`record_id = osticket-<ticket_id>`, `source_ref_id` = osTicket ticket number, `source_url` = link to the ticket).
- Updates in osTicket (title, body, priority, status, team, closed date) are refreshed on every sync. AI tagging and curation fields are never overwritten.
- Tickets deleted in osTicket are soft-flagged (`source_metadata.removed = true`) and hidden from the UI list and retrieval; a ticket that reappears is un-flagged. The removal step is skipped if osTicket returns zero tickets.
- Synced rows start as `NeedsRetag` with confidence 0 and placeholder `module`/`category` (the table requires them) until the AI tagger runs.
- osTicket's core HTTP API is create-only (`POST /api/tickets.json`, `POST /api/tasks/cron`), so the sync reads osTicket's MySQL database through a **read-only** user. A webhook plugin or the Extended API plugin would remove that dependency.

**Severity** uses osTicket's priority names everywhere: `Low`, `Normal`, `High`, `Emergency`. Existing databases need `db/migrations/2026-11-17_osticket_severity.sql` applied once (converts old `SEV1-Critical`..`SEV4-Low` values and the CHECK constraints on all four record tables).

**Backend config** (see `sample.env`):
| Variable | Purpose |
|---|---|
| `OSTICKET_URL` | osTicket base URL, used to build per-ticket links |
| `OSTICKET_DB_HOST/PORT/NAME` | osTicket MySQL (default `127.0.0.1:3306`, db `osticket`) |
| `OSTICKET_DB_USER/PASSWORD` | a **read-only** MySQL user (`GRANT SELECT` only) |
| `OSTICKET_SYNC_INTERVAL` | seconds between automatic syncs; leave empty for manual only |

UI: set `VITE_OSTICKET_URL` (defaults to `http://127.0.0.1:8081`) for the New Ticket link.

**Run a sync**
```bash
python -m app.services.osticket --dry-run     # show what would sync, writes nothing
curl -X POST localhost:8000/api/osticket/sync # run it (add ?dry_run=true to preview)
curl localhost:8000/api/osticket/status       # is the DB configured?
```

**Run osTicket locally without Docker** (Debian/Ubuntu; for development only, use a proper web server in production)
```bash
sudo apt-get install -y mariadb-server php-cli php-mysql php-mbstring php-xml php-gd php-intl php-apcu php-imap php-curl php-zip
sudo mysql -e "CREATE DATABASE osticket CHARACTER SET utf8mb4;
  CREATE USER 'osticket'@'localhost' IDENTIFIED BY '<pw>'; GRANT ALL ON osticket.* TO 'osticket'@'localhost';
  CREATE USER 'osticket_ro'@'localhost' IDENTIFIED BY '<ro-pw>'; GRANT SELECT ON osticket.* TO 'osticket_ro'@'localhost';"
# download osTicket v1.18.x from https://github.com/osTicket/osTicket/releases, unzip, copy upload/ to ~/osticket
cp ~/osticket/include/ost-sampleconfig.php ~/osticket/include/ost-config.php && chmod 666 ~/osticket/include/ost-config.php
php -S 127.0.0.1:8081 -t ~/osticket                # then open http://127.0.0.1:8081/setup/ and run the installer
# after install: chmod 644 include/ost-config.php and remove/move the setup/ folder
```
Dev-server notes: PHP's built-in server reports `cli-server`, which makes osTicket's API read stdin instead of the request body, so `POST /api/tickets.json` needs a local patch (`include/class.api.php` constructor: `$this->sapi = $sapi ?: 'http';`) plus a router script mapping `/api/*` to `api/http.php`. Use Apache/nginx + PHP-FPM to avoid both. osTicket's default ban list blocks `test@example.com`. The osTicket install lives outside this repo and is not committed.

**Create the taxonomy in osTicket** (module topics and the category list). Names come from `backend/taxonomy.py`, so they cannot drift from the AI app. Needs `php` and filesystem access to the osTicket install; safe to re-run (existing items are left alone).
```bash
python scripts/osticket_setup_taxonomy.py --dry-run     # preview
python scripts/osticket_setup_taxonomy.py --osticket-dir ~/osticket
```
It creates, via osTicket's own PHP classes (`scripts/osticket/setup_taxonomy.php`): 8 help topics (one per module, e.g. `PolicyCenter`), an `Issue Category` custom list with the 9 categories, and an agent-only `Issue Category` dropdown on the Ticket Details form (requesters pick the module; agents/AI set the category). It also rebuilds osTicket's `ost_ticket__cdata` table so the new column is queryable. osTicket's stock topics (General Inquiry, Feedback, Report a Problem) are left in place; disable them in the admin panel if you only want module topics.

**Mapping osTicket to the taxonomy.** osTicket has no ticket category/sub-category. Plan: help topic = module (8), a custom dropdown list field on the ticket form = category (9), osTicket priority = severity, osTicket teams = assignee team. The sync maps a help topic to `module` when its name matches a module exactly; it does not read the `Issue Category` field yet, so category still comes from the AI tagger.
