# ThingBD CRM

ThingBD's ([thingbd.com](https://thingbd.com/)) multi-tenant CRM built on Django, with a workflow automation engine (trigger → conditions → actions) planned for Phase 4. Branding follows the ThingBD site: violet `#8B5CF6`, near-black `#050505` / `#121118`, Outfit font, "T" mark (`static/img/favicon.svg`).

**Stack:** Django 5.2 · PostgreSQL (SQLite for quick local runs) · Celery + Redis · Tailwind + HTMX · Select2 · DataTables (server-side) · FullCalendar · Docker Compose

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
| 7 | REST API, outgoing webhooks, integrations (WhatsApp, Facebook lead ads, Google Calendar) | next |
| 8 | Hardening, backups, production deploy | |

### Automation coverage

| Area | Already built | Added to the roadmap |
|---|---|---|
| **Lead management** | Contact capture (manual, CSV, webhook), dedupe, lifecycle auto-advance from deals, owner assignment incl. round robin, "inactive N days" trigger | Phase 5: web forms with spam protection, lead scoring rules + hot/warm/cold grades, "lead not contacted within N hours" (speed-to-lead), segments |
| **Workflow automation** | Triggers → conditions → actions, waits, "continue only if", run logs, webhooks in/out, loop protection | Phase 5: triggers for form submitted, lead grade changed, date fields (birthdays/renewals), deal idle; actions for sequences and score; sends pause outside quiet hours |
| **SMS & communication** | 1:1 email/SMS, templates, inbox, open/click tracking, IMAP + Twilio inbound, workflow sends | Phase 5: multi-step email/SMS sequences that stop on reply, bulk broadcasts to segments, unsubscribe link + List-Unsubscribe, SMS STOP/START, quiet hours. Phase 7: WhatsApp |
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

## Run with Docker (Postgres + Redis + real Celery worker)

```bash
docker compose up --build
```

Services: `migrate` (runs once, first), `web` (:8000), `worker`, `beat`, `db` (Postgres 16), `redis`. After changing `requirements.txt`, run `docker compose up --build`; Docker's Postgres is a separate database from your local SQLite. Then visit **System status** as an admin and click *Run test task* to confirm the worker answers.

## Tests

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
static/js/ui.js    Select2 on every form select, AJAX pickers, DataTables (client + server side); static/css/ui.css themes them
templates/         base + app/auth layouts, shared form partial
```

## Who can do what

| | Agent | Manager | Admin |
|---|:-:|:-:|:-:|
| View / create / edit contacts, companies, deals, notes, files; move/win/lose deals; tasks; bulk tag/owner/stage | ✓ | ✓ | ✓ |
| Delete contacts, companies & deals, bulk delete, CSV import & export, manage tags, pipelines & lost reasons | | ✓ | ✓ |
| Manage custom fields, members, org settings | | | ✓ |

Notes and files can be deleted by their author or any manager. Tasks can be edited/deleted by their assignee, creator or a manager; anyone can tick one off. Uploaded files are private: they're served only through an org-checked download view, never from `MEDIA_URL`.

## Deals and the contact lifecycle

Won/lost is a deal **status**, not a stage, so closed deals keep the stage they were in and every pipeline gets won/lost for free. Contacts move forward automatically: creating a deal turns a Lead/Qualified contact into an Opportunity, and winning a deal makes them a Customer. Nothing ever moves a contact backwards.

## Task reminders

Reminders are emailed by `apps.tasks.tasks.send_task_reminders`, which Celery beat runs every minute (the `beat` service in Docker Compose). Without beat, schedule `python manage.py send_task_reminders` with cron. Set `SITE_URL` so the email links point at the real site.

## UI building blocks

- **Select2** is applied automatically to every `<select>` inside a `.form-stack` form (opt out with `data-no-select2`). Big FK pickers use `apps.core.widgets.LookupWidget`, which renders a Select2 AJAX picker fed by a `{"results": [{id, text, hint}]}` endpoint.
- **DataTables**: big lists use server-side processing. Subclass `apps.core.datatables.DataTableView`, list `Column`s (render with `apps.core.cells` helpers, which escape everything) and point `<table data-dt-source data-dt-filters="#filters">` at it. Small tables just add `data-datatable`.
- **FullCalendar** at `/tasks/calendar/`: tasks by due time and open deals by expected close date; drag to reschedule, click a day to add a task.

## Email & SMS

Admins set it up under **Settings → Email & SMS**:
- **Outgoing email**: your own SMTP (Gmail/Workspace, cPanel mail, etc.) or the platform default. Opens (pixel) and clicks (redirect through `/t/c/<token>/`) are tracked per message.
- **Incoming email**: IMAP polling every 2 minutes (Celery beat). Replies thread into the original conversation via `In-Reply-To`/`References`; unknown senders appear as "New sender" and can be saved as contacts.
- **SMS**: Twilio (with signed inbound webhook) or any HTTP gateway URL with `{to}`, `{message}`, `{sender}`, `{api_key}` (BulkSMSBD, SSL Wireless, …). Numbers are normalised to `8801XXXXXXXXX`. The *Console* provider prints instead of sending for development.
- SMTP/IMAP passwords and SMS keys are encrypted with `FIELD_ENCRYPTION_KEY`. Set it once per environment; changing it means re-entering them.
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

## Timeline events → automations

Every change to a contact, company or deal goes through `apps.activities.services.record()` (created, updated, stage/owner changed, tag added/removed, note, file, imported, deal moved/won/lost/reopened, task created/completed, email sent/received/opened/clicked, SMS sent/received, message failed, call logged, form submitted, lead grade changed, unsubscribed/resubscribed, added to / left sequence). Phase 4's automation triggers will subscribe there, so new features should log through it rather than writing `Activity` rows directly.

## Multi-tenancy rules (read before adding Phase 1+ models)

- Every tenant-owned model extends `apps.core.models.OrgScopedModel` (adds `org` FK + `for_org()`).
- Views use `OrgMemberRequiredMixin` / `ManagerRequiredMixin` / `AdminRequiredMixin` and **always** filter with `Model.objects.for_org(request.org)` — never `.all()`.
- `request.org` and `request.membership` are set by `CurrentOrganizationMiddleware`; users switch org from the top bar.
- Role ranks: agent < manager < admin. `membership.has_role(Role.MANAGER)` is true for managers and admins.
