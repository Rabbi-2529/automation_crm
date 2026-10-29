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
| 4 | Automation engine | next |
| 5 | Web forms, lead scoring, drip campaigns | |
| 6 | Reports & dashboards | |
| 7 | REST API, webhooks, integrations | |
| 8 | Hardening, backups, production deploy | |

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

Services: `web` (:8000), `worker`, `beat`, `db` (Postgres 16), `redis`. Then visit **System status** as an admin and click *Run test task* to confirm the worker answers.

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

## Timeline events → automations

Every change to a contact, company or deal goes through `apps.activities.services.record()` (created, updated, stage/owner changed, tag added/removed, note, file, imported, deal moved/won/lost/reopened, task created/completed, email sent/received/opened/clicked, SMS sent/received, message failed, call logged). Phase 4's automation triggers will subscribe there, so new features should log through it rather than writing `Activity` rows directly.

## Multi-tenancy rules (read before adding Phase 1+ models)

- Every tenant-owned model extends `apps.core.models.OrgScopedModel` (adds `org` FK + `for_org()`).
- Views use `OrgMemberRequiredMixin` / `ManagerRequiredMixin` / `AdminRequiredMixin` and **always** filter with `Model.objects.for_org(request.org)` — never `.all()`.
- `request.org` and `request.membership` are set by `CurrentOrganizationMiddleware`; users switch org from the top bar.
- Role ranks: agent < manager < admin. `membership.has_role(Role.MANAGER)` is true for managers and admins.
