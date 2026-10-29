# ThingBD CRM

ThingBD's ([thingbd.com](https://thingbd.com/)) multi-tenant CRM built on Django, with a workflow automation engine (trigger → conditions → actions) planned for Phase 4. Branding follows the ThingBD site: violet `#8B5CF6`, near-black `#050505` / `#121118`, Outfit font, "T" mark (`static/img/favicon.svg`).

**Stack:** Django 5.2 · PostgreSQL (SQLite for quick local runs) · Celery + Redis · Django REST Framework + OpenAPI · Tailwind + HTMX · Select2 · DataTables (server-side) · FullCalendar · Chart.js · Docker Compose

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Foundation: email login, workspaces (orgs), roles, teams, Celery, Docker | ✅ done |
| 1 | Contacts, companies, tags, custom fields, notes, files, timeline, CSV import/export | ✅ done |
| 2 | Pipelines, deals, kanban, won/lost, tasks & email reminders | ✅ done |
| 3 | Email & SMS (SMTP/IMAP, Twilio, BD HTTP gateways), templates, inbox, open/click tracking, call logs, calendar | ✅ done |
| 4 | Automation engine: triggers, conditions, actions, waits, builder, run logs, webhooks | ✅ done |
| 5 | **Lead management & communication automation**: web forms, lead scoring & grades, segments, email/SMS sequences, broadcasts, unsubscribe & SMS STOP, quiet hours, speed-to-lead / date / deal-idle triggers | ✅ done |
| 6 | **Sales & reporting automation**: sales dashboard with charts, funnel & win rate, forecast, rep leaderboard, targets & attainment, scheduled email reports (team summary + rep pipeline digest), CSV exports | ✅ done |
| 7 | **REST API & integrations**: API keys + REST API with Swagger docs, signed outgoing webhooks with retries, WhatsApp Cloud API (send, inbox, templates, workflows), Facebook lead ads, Google Calendar subscription feed | ✅ done |
| 8 | **Hardening & deploy**: production Docker stack (gunicorn + Caddy auto-HTTPS), compiled Tailwind + vendored assets, login throttling, strict production settings, nightly backups with restore, branded error pages, CI, demo seed | ✅ done |

### Automation coverage

| Area | Already built | Added to the roadmap |
|---|---|---|
| **Lead management** | Contact capture (manual, CSV, webhook, REST API, Facebook lead ads), dedupe, lifecycle auto-advance from deals, owner assignment incl. round robin, "inactive N days" trigger | Phase 5: web forms with spam protection, lead scoring rules + hot/warm/cold grades, "lead not contacted within N hours" (speed-to-lead), segments |
| **Workflow automation** | Triggers → conditions → actions, waits, "continue only if", run logs, webhooks in/out, loop protection | Phase 5: triggers for form submitted, lead grade changed, date fields (birthdays/renewals), deal idle; actions for sequences and score; sends pause outside quiet hours |
| **SMS & communication** | 1:1 email/SMS, templates, inbox, open/click tracking, IMAP + Twilio inbound, workflow sends | Phase 5: multi-step email/SMS sequences that stop on reply, bulk broadcasts to segments, unsubscribe link + List-Unsubscribe, SMS STOP/START, quiet hours. Phase 7 (done): WhatsApp 1:1, inbox, approved templates, workflow action + trigger |
| **Sales & reporting** | Pipeline board totals, weighted forecast, dashboard KPIs, deal close-soon / idle triggers | Phase 6 (done): reports dashboard, funnel/win rate, forecast, leaderboard, targets, scheduled report emails, CSV exports |

## Run locally (no Docker)

Uses SQLite, and Celery tasks run inline (`CELERY_TASK_ALWAYS_EAGER=True`), so no Postgres or Redis is needed.

```bash
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py runserver
```

Open http://localhost:8000/accounts/signup/ (the preview config in `.claude/launch.json` uses port 8020 because 8000 is often taken) to create your first workspace. Emails (invites, password resets) print to the console.

### Demo access

Put `DEMO_EMAIL` and `DEMO_PASSWORD` (8+ characters) in `.env`, then:

```bash
.venv/Scripts/python manage.py seed_demo
```

It creates the **Demo Traders** workspace with three logins that all use `DEMO_PASSWORD`: `DEMO_EMAIL` (admin), `manager@<same domain>` and `agent@<same domain>`, plus sample companies, contacts, deals (won, lost, open), tasks, a team and a monthly target. Run it again any time; existing records are left alone (`--reset-password` sets the password again). In Docker: `docker compose exec web python manage.py seed_demo`. It refuses to run with `DEBUG` off unless you pass `--allow-production`.

## Run with Docker (Postgres + Redis + real Celery worker)

```bash
cp .env.example .env
docker compose up -d
```

`docker compose up -d` rebuilds the image first (`pull_policy: build`, cached, ~20 s when nothing changed), so packages added to `requirements.txt` are always installed, and the `migrate` service applies new migrations before web/worker/beat start. If it ever reports `service "migrate" didn't complete successfully`, read why with `docker compose logs migrate`.

Services: `migrate` (runs once, first), `web` (:8000), `worker`, `beat`, `db` (Postgres 16), `redis`. Docker's Postgres is a separate database from your local SQLite. Visit **System status** as an admin and click *Run test task* to confirm the worker answers.

Optional free AI (open-source models via Ollama) for the help assistant and reply drafts:

```bash
docker compose --profile ai up -d
docker compose exec ollama ollama pull llama3.2:3b
```

## Deploy to production

One Linux server (2 vCPU / 4 GB is plenty to start) with Docker, and a domain whose DNS **A record** points at it.

```bash
git clone https://github.com/Rabbi-2529/automation_crm.git && cd automation_crm
cp .env.prod.example .env.prod      # fill in every CHANGE-ME (see the comments for how to generate keys)
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py createsuperuser
```

Then sign up at `https://<your domain>/accounts/signup/` to create the first workspace.

What runs (`docker-compose.prod.yml`): **caddy** (ports 80/443, gets and renews the HTTPS certificate, redirects HTTP), **web** (gunicorn, health-checked), **worker** + **beat** (Celery), **db** (Postgres 16) and **redis** on the internal network only, **migrate** (runs first on every deploy) and **backup**. The image compiles Tailwind and vendors every JS/CSS library at build time (`frontend/`), so production pages load nothing from CDNs except Google Fonts.

To update: `git pull && docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build`. Logs: `docker compose -f docker-compose.prod.yml logs -f web worker`.

### Security in production (`config/settings/prod.py`)

- Refuses to start without a long `SECRET_KEY`, `ALLOWED_HOSTS` and `FIELD_ENCRYPTION_KEY`. Keep a copy of `FIELD_ENCRYPTION_KEY` with your backups: without it saved SMTP/SMS/WhatsApp/AI keys can't be read.
- HTTPS only: secure cookies, HSTS (1 year), `X-Frame-Options: DENY` (public web forms opt out so they can be embedded), nosniff, same-origin referrer. `manage.py check --deploy` passes (CI checks it).
- **Login throttling**: 5 failed logins for one email from one IP lock that pair for 15 minutes (20 failures lock the IP); password-reset emails are limited to 5 an hour per IP. Counts live in Redis, shared by all workers.
- The admin site lives at `ADMIN_URL`, not `/admin/`. Server errors are emailed to `ADMINS`.
- Branded 400/403/404/429/500 pages that don't depend on the database or static files.

### Backups

The **backup** service dumps the database (`pg_dump`, custom format) and the uploaded files every night at `BACKUP_HOUR` into the `backups` volume and keeps `BACKUP_KEEP_DAYS` days. **System status** shows the latest backup and turns red if it's more than 36 hours old.

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec backup sh /backup.sh once      # back up now
docker compose -f docker-compose.prod.yml --env-file .env.prod exec backup sh /backup.sh list      # what's there
```

Copy backups off the server too (another machine or cloud storage), e.g. a nightly `docker cp thingbd-crm-backup-1:/backups ./crm-backups` from cron, or `rclone`.

**Restore** (replaces the database):

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod stop web worker beat
docker compose -f docker-compose.prod.yml --env-file .env.prod exec backup sh /backup.sh restore crm-YYYYmmdd-HHMMSS.dump --yes
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
```

Uploaded files: `tar -xzf media-….tgz` into the `media` volume. (On Git Bash for Windows, prefix `exec … /backup.sh` commands with `MSYS_NO_PATHCONV=1`.)

## Tests

CI (`.github/workflows/ci.yml`) runs the tests, checks for missing migrations, runs `check --deploy` against the production settings and builds the production image on every push.

```bash
.venv/Scripts/python manage.py test --settings=config.settings.test
```

## Layout

```
config/            settings (base/dev/test/prod), urls, celery app
apps/core/         TimeStampedModel, OrgScopedModel, dashboard, health + system status, test factories
apps/accounts/     email-login User, signup (creates user + workspace), profile, password flows
apps/orgs/         Organization, Membership (admin/manager/agent), Team, current-org middleware, role mixins
apps/contacts/     Contact, Company, Tag, CustomFieldDefinition, ImportJob; list filters, CSV importer (Celery) + export
apps/activities/   Activity timeline, Note, Attachment; services.record() is the single place timeline events are written
apps/pipeline/     Pipeline, Stage, Deal, LostReason; kanban board, won/lost, services.py owns every deal state change
apps/tasks/        Task with due time + reminder; Celery beat job (or `manage.py send_task_reminders`) emails reminders; FullCalendar feed
apps/messaging/    ChannelSettings (encrypted secrets), templates, Conversation/Message, tracking, IMAP + Twilio inbound, call logs
apps/automation/   Workflow/Action/Run/StepLog; registry.py (vocabulary), engine.py, actions.py, conditions.py, builder UI
apps/leads/        Web forms (public /f/<token>/, embeddable), lead scoring rules, score log, grade thresholds
apps/campaigns/    Segments (SQL-evaluated conditions), sequences + enrollments, broadcasts + recipients
apps/reports/      metrics.py (one Scope → KPIs, series, funnel, forecast, leaderboard), targets, scheduled report emails
apps/integrations/ API keys + REST API (api/), outgoing webhooks, Facebook lead ads, iCalendar feed
deploy/            Caddyfile and backup.sh for docker-compose.prod.yml
frontend/          assets.json (every JS/CSS library, pinned), tailwind.css, build.mjs: the production image's front-end build
apps/assistant/    AI help assistant + reply drafts: providers.py (OpenAI-compatible: Ollama/Groq/OpenRouter/…), help_articles.py
static/js/ui.js    Select2 on every form select, AJAX pickers, DataTables (client + server side); static/css/ui.css themes them
templates/         base + app/auth layouts, shared form partial
```

## Who can do what

| | Agent | Manager | Admin |
|---|:-:|:-:|:-:|
| View / create / edit contacts, companies, deals, notes, files; move/win/lose deals; tasks; bulk tag/owner/stage | ✓ | ✓ | ✓ |
| Delete contacts, companies & deals, bulk delete, CSV import & export, manage tags, pipelines & lost reasons | | ✓ | ✓ |
| Manage custom fields, members, org settings, messaging channels, integrations (API keys, webhooks, Facebook) | | | ✓ |

Notes and files can be deleted by their author or any manager. Tasks can be edited/deleted by their assignee, creator or a manager; anyone can tick one off. Uploaded files are private: they're served only through an org-checked download view, never from `MEDIA_URL`.

## Deals and the contact lifecycle

Won/lost is a deal **status**, not a stage, so closed deals keep the stage they were in and every pipeline gets won/lost for free. Contacts move forward automatically: creating a deal turns a Lead/Qualified contact into an Opportunity, and winning a deal makes them a Customer. Nothing ever moves a contact backwards.

## Task reminders

Reminders are emailed by `apps.tasks.tasks.send_task_reminders`, which Celery beat runs every minute (the `beat` service in Docker Compose). Without beat, schedule `python manage.py send_task_reminders` with cron. Set `SITE_URL` so the email links point at the real site.

## UI building blocks

- **Select2** is applied automatically to every `<select>` inside a `.form-stack` form (opt out with `data-no-select2`). Big FK pickers use `apps.core.widgets.LookupWidget`, which renders a Select2 AJAX picker fed by a `{"results": [{id, text, hint}]}` endpoint.
- **DataTables**: big lists use server-side processing. Subclass `apps.core.datatables.DataTableView`, list `Column`s (render with `apps.core.cells` helpers, which escape everything) and point `<table data-dt-source data-dt-filters="#filters">` at it. Small tables just add `data-datatable`.
- **FullCalendar** at `/tasks/calendar/`: tasks by due time and open deals by expected close date; drag to reschedule, click a day to add a task.

## Email, SMS & WhatsApp

Admins set it up under **Settings → Messaging channels**:
- **Outgoing email**: your own SMTP (Gmail/Workspace, cPanel mail, etc.) or the platform default. Opens (pixel) and clicks (redirect through `/t/c/<token>/`) are tracked per message.
- **Incoming email**: IMAP polling every 2 minutes (Celery beat). Replies thread into the original conversation via `In-Reply-To`/`References`; unknown senders appear as "New sender" and can be saved as contacts.
- **SMS**: Twilio (with signed inbound webhook) or any HTTP gateway URL with `{to}`, `{message}`, `{sender}`, `{api_key}` (BulkSMSBD, SSL Wireless, …). Numbers are normalised to `8801XXXXXXXXX`. The *Console* provider prints instead of sending for development.
- **WhatsApp**: Meta's WhatsApp Cloud API (phone number ID, permanent access token, app secret). Point the Meta app's webhook at the URL + verify token shown on the WhatsApp tab; incoming messages land in the inbox and delivery receipts mark failed/read messages. Meta only allows free text within 24 hours of the contact's last message; after that, use a WhatsApp message template whose *WhatsApp template name* matches one approved in WhatsApp Manager (its variables fill `{{1}}`, `{{2}}`…). STOP/START replies opt the contact out of / back into WhatsApp. *Console* prints instead of sending. Sequences and broadcasts stay email/SMS for now (bulk WhatsApp needs approved marketing templates).
- SMTP/IMAP passwords, SMS keys and WhatsApp/Facebook tokens are encrypted with `FIELD_ENCRYPTION_KEY`. Set it once per environment; changing it means re-entering them.
- Templates support `{{ contact.first_name }}`, `{{ deal.value }}`, `{{ user.full_name }}` and more, with `|default:"…"` fallbacks. Only whitelisted values are reachable.

## Automations (Phase 4)

**Automations** in the sidebar. A workflow is *trigger → optional conditions → ordered steps*:

- **Triggers**: contact created / field changed / lifecycle changed / tag added / owner changed / inactive for N days; deal created / moved / won / lost / closing in N days; task completed; email opened / clicked / received; SMS received; call logged; incoming webhook.
- **Conditions**: any contact/deal field incl. custom fields, tags, owner, days since created / last activity; all-or-any.
- **Steps**: send email/SMS (as the record owner or workflow creator), email a teammate, create task, assign owner (specific or round robin), set lifecycle, update field, add/remove tag, add note, create deal, move deal, signed webhook (private IPs blocked), **wait**, **continue only if…**
- Each record runs once unless re-enrollment is allowed. Workflows may trigger each other up to 3 levels deep and never re-trigger themselves.
- Runs and every step are logged (Automations → workflow → Log). "Run it now" tests a workflow on one contact. Four recipes get you started.
- Engine: `activities.services.record()` → `automation.events.dispatch()` (only queues work if a matching live workflow exists) → Celery `process_activity`. Beat resumes waits every minute and scans time-based triggers every 15 minutes.

## Lead & communication automation (Phase 5)

- **Web forms** (Marketing → Web forms): pick fields (incl. custom fields), hosted link + iframe embed that auto-resizes, UTM capture, consent checkbox, default owner/tags. New people become contacts; known emails/phones only get blanks filled. Spam: hidden honeypot, signed render-time check (< 2 s = bot), 10 submissions / IP / 10 min. Bots get the normal thank-you page.
- **Lead scoring** (Marketing → Lead scoring): *profile* rules (points while conditions match, recalculated) + *behaviour* rules (points per event, optional cap). Grades cold / warm / hot with your thresholds; a grade change is a timeline event and a workflow trigger. Score shows on contacts (sortable column, filter, contact card with recent score changes).
- **Segments**: saved, always-current contact lists using the same conditions as workflows, evaluated in SQL. Filter the contacts list by segment; use them for broadcasts, bulk sequence enrollment and the "In segment" condition.
- **Sequences**: multi-step email/SMS with delays; enroll from a contact, a whole segment or the "Add to sequence" workflow step. Stops on reply (optional), on unsubscribe, or when exit conditions match. Per-step sent / opened / clicked stats.
- **Broadcasts**: one email/SMS to a segment, now or scheduled; 200 per minute; skips unsubscribed / no-address contacts; stats per recipient.
- **Consent**: marketing emails get an unsubscribe link plus `List-Unsubscribe` / one-click headers; marketing SMS can append "Reply STOP to opt out"; inbound STOP / UNSUBSCRIBE / বন্ধ opt out, START opts back in; manual toggles on the contact page.
- **Quiet hours** (Settings → Email & SMS → Sending rules): workflow sends, sequences and broadcasts wait for the sending window. Messages you write yourself go immediately.
- **New workflow triggers**: web form submitted, lead grade changes, lead not contacted within N hours (speed to lead), date field such as birthdays/renewals (optionally yearly), deal idle N days. **New steps**: add to / remove from sequence, change lead score. **New conditions**: lead score, grade, unsubscribed (email/SMS), in segment.
- Background jobs: sequences and broadcasts are processed every minute by Celery beat; scoring runs on each timeline event.

## Sales & reporting (Phase 6)

**Reports** in the sidebar (everyone can view; exports and targets are for managers):

- **Filters** on every report: period (this/last month or quarter, last 30/90 days, this year, custom), pipeline, and people (everyone, me, a team, one rep). All numbers use the workspace time zone.
- **KPIs**: revenue won (vs the previous period), deals won, win rate (won ÷ won + lost closed in the period), average deal, average sales cycle, new deals, open pipeline + weighted forecast, new leads and how many became customers. Team revenue target progress when one is set.
- **Charts** (Chart.js, each with a Table view): revenue won over time, stage funnel (how far deals created in the period got, using stage history), forecast by expected close month (value vs weighted), lost reasons, lead sources.
- **Leaderboard**: revenue, won/lost, win rate, open pipeline, new deals, emails, SMS, calls, tasks completed, target attainment per rep. CSV export.
- **Targets**: monthly or quarterly goals for the team or a rep (revenue, deals won, new deals, new leads), with live progress.
- **Report emails** (Reports → Email me reports): a daily/weekly/monthly **pipeline digest** for each rep (closing this week, overdue, going quiet, tasks due) and a **team sales summary** for managers. Sent by Celery beat (checked every 15 minutes) at the hour each person picks.
- **Closed deals CSV** for the filtered period (days to close, lost reason, owner…).

Chart colours were checked with a colour-blindness/contrast validator: violet `#7c3aed` for single series, plus orange `#eb6834` as the second series.

## REST API & integrations (Phase 7)

**Settings → Integrations** (admins):

- **REST API** at `/api/v1/` with interactive docs at `/api/docs/` (OpenAPI schema at `/api/v1/schema/`). Create a key, then send `Authorization: Bearer tbd_…` (or `X-Api-Key`). Keys are stored hashed and shown once, can be read-only or read/write, act as the person who made them (their role; deleting needs a manager's key) and stop working if that person leaves. 120 requests a minute per key (`API_RATE_LIMIT`).
  - `contacts` (+ `POST contacts/upsert/` matched by email, then phone), `companies`, `deals` (+ `won/`, `lost/`, `reopen/`), `tasks` (+ `complete/`, `reopen/`), `notes`, `activities` (the timeline), and read-only `pipelines`, `tags`, `members`, `me`.
  - Writes go through the same services as the app, so the timeline, lifecycle moves, automations and webhooks all fire. Filters: `search`, `email`, `phone`, `tag`, `owner`, `updated_since`…; pages of 50 (`page_size` up to 200).
- **Outgoing webhooks**: pick timeline events (or all); each is POSTed as JSON with the contact/deal/company in the API's shape. Signed with `X-ThingBD-Signature: t=<unix>,v1=<HMAC-SHA256(secret, t + "." + body)>`. Retries after 1 min, 5 min, 30 min, 2 h, 6 h; 50 failures in a row switch the endpoint off. Delivery log with redeliver and a test button. Private/localhost URLs are refused (SSRF) unless `ALLOW_PRIVATE_WEBHOOKS` (on in dev settings).
- **Facebook lead ads**: add the Meta app secret, connect pages (Page ID + page access token with `leads_retrieval`), and subscribe the app's Page webhook (`leadgen`) to the URL + verify token shown. Each lead is fetched from the Graph API and creates or updates the contact (source *Facebook lead ad*, default owner, tags); unknown questions go into a note, or into a custom field with the same key. Failed fetches are retried every 15 minutes (up to 5 times) and can be retried by hand. Workflows can start on *Facebook lead ad is submitted* (optionally for one page).
- **Google Calendar** (everyone): Calendar → *Sync to Google Calendar* gives each person a private iCalendar link with their tasks and open deals' close dates; subscribe from Google, Outlook or Apple Calendar. One-way; the link can be rotated or turned off.

## Help & AI assistant

- **Sidebar**: Dashboard and Help, then collapsible **CRM**, **Marketing** and **Workspace** groups. The group of the current page opens by itself (otherwise the last one you opened); opening one closes the others. The menu scrolls on short screens. A red dot on a closed group means overdue tasks or unread messages.
- **Help center** (`/help/`): short articles for every feature, searchable, with "Go to page" links. They're in `apps/assistant/help_articles.py`; keep them in step with the app.
- **✨ Help** button on every page: staff ask in English or Bangla. The AI answers only from the matching help articles (and the person's role), links the articles it used, and staff can rate answers. With no AI, or if the AI is down, the matching articles are shown instead.
- **Draft with AI** in the inbox: a suggested reply from the last 8 messages plus *About your business*; channel-aware (email / SMS length / WhatsApp). It only fills the reply box; staff edit and send it themselves.
- **Choosing the AI** (admins: **Workspace → AI assistant**, or platform-wide with `AI_PROVIDER`, `AI_BASE_URL`, `AI_MODEL`, `AI_API_KEY`): built-in help only, **Ollama** (free open-source models on your own server; nothing leaves it), **Groq** or **OpenRouter** (free tiers of open-source models), or any OpenAI-compatible API. Everything uses the OpenAI chat-completions format, so moving to another or a paid model later is a settings change. Per-person daily limit, usage and errors are shown on the settings page; "Test connection" checks it.

## Timeline events → automations

Every change to a contact, company or deal goes through `apps.activities.services.record()` (created, updated, stage/owner changed, tag added/removed, note, file, imported, deal moved/won/lost/reopened, task created/completed, email sent/received/opened/clicked, SMS sent/received, message failed, call logged, form submitted, lead grade changed, unsubscribed/resubscribed, added to / left sequence, WhatsApp sent/received, Facebook lead ad). Automations, lead scoring and outgoing webhooks all subscribe there. Phase 4's automation triggers will subscribe there, so new features should log through it rather than writing `Activity` rows directly.

## Multi-tenancy rules (read before adding Phase 1+ models)

- Every tenant-owned model extends `apps.core.models.OrgScopedModel` (adds `org` FK + `for_org()`).
- Views use `OrgMemberRequiredMixin` / `ManagerRequiredMixin` / `AdminRequiredMixin` and **always** filter with `Model.objects.for_org(request.org)` — never `.all()`.
- `request.org` and `request.membership` are set by `CurrentOrganizationMiddleware`; users switch org from the top bar.
- Role ranks: agent < manager < admin. `membership.has_role(Role.MANAGER)` is true for managers and admins.
