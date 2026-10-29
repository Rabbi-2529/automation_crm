# Automation CRM

Multi-tenant CRM built on Django, with a workflow automation engine (trigger → conditions → actions) planned for Phase 4.

**Stack:** Django 5.2 · PostgreSQL (SQLite for quick local runs) · Celery + Redis · HTMX + Tailwind templates · Docker Compose

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Foundation: email login, workspaces (orgs), roles, teams, Celery, Docker | ✅ done |
| 1 | Contacts, companies, tags, custom fields, timeline, CSV import | next |
| 2 | Pipelines, deals, kanban, tasks | |
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

Open http://localhost:8000/accounts/signup/ to create your first workspace. Emails (invites, password resets) print to the console.

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
templates/         base + app/auth layouts, shared form partial
```

## Multi-tenancy rules (read before adding Phase 1+ models)

- Every tenant-owned model extends `apps.core.models.OrgScopedModel` (adds `org` FK + `for_org()`).
- Views use `OrgMemberRequiredMixin` / `ManagerRequiredMixin` / `AdminRequiredMixin` and **always** filter with `Model.objects.for_org(request.org)` — never `.all()`.
- `request.org` and `request.membership` are set by `CurrentOrganizationMiddleware`; users switch org from the top bar.
- Role ranks: agent < manager < admin. `membership.has_role(Role.MANAGER)` is true for managers and admins.
