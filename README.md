# ThingBD CRM

ThingBD's ([thingbd.com](https://thingbd.com/)) multi-tenant CRM built on Django, with a workflow automation engine (trigger → conditions → actions) planned for Phase 4. Branding follows the ThingBD site: violet `#8B5CF6`, near-black `#050505` / `#121118`, Outfit font, "T" mark (`static/img/favicon.svg`).

**Stack:** Django 5.2 · PostgreSQL (SQLite for quick local runs) · Celery + Redis · HTMX + Tailwind templates · Docker Compose

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Foundation: email login, workspaces (orgs), roles, teams, Celery, Docker | ✅ done |
| 1 | Contacts, companies, tags, custom fields, notes, files, timeline, CSV import/export | ✅ done |
| 2 | Pipelines, deals, kanban, tasks | next |
| 3 | Email & SMS, templates, inbox | |
| 4 | Automation engine | |
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
templates/         base + app/auth layouts, shared form partial
```

## Who can do what (Phase 1)

| | Agent | Manager | Admin |
|---|:-:|:-:|:-:|
| View / create / edit contacts & companies, notes, files, bulk tag/owner/stage | ✓ | ✓ | ✓ |
| Delete contacts & companies, bulk delete, CSV import & export, manage tags | | ✓ | ✓ |
| Manage custom fields, members, org settings | | | ✓ |

Notes and files can be deleted by their author or any manager. Uploaded files are private: they're served only through an org-checked download view, never from `MEDIA_URL`.

## Timeline events → automations

Every change to a contact/company goes through `apps.activities.services.record()` (created, updated, stage/owner changed, tag added/removed, note, file, imported). Phase 4's automation triggers will subscribe there, so new features should log through it rather than writing `Activity` rows directly.

## Multi-tenancy rules (read before adding Phase 1+ models)

- Every tenant-owned model extends `apps.core.models.OrgScopedModel` (adds `org` FK + `for_org()`).
- Views use `OrgMemberRequiredMixin` / `ManagerRequiredMixin` / `AdminRequiredMixin` and **always** filter with `Model.objects.for_org(request.org)` — never `.all()`.
- `request.org` and `request.membership` are set by `CurrentOrganizationMiddleware`; users switch org from the top bar.
- Role ranks: agent < manager < admin. `membership.has_role(Role.MANAGER)` is true for managers and admins.
