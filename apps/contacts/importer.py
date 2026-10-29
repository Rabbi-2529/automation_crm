"""CSV import/export for contacts. The import runs in Celery (see tasks.py) and reports progress on ImportJob."""

import csv
import io

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.activities.models import ActivityKind
from apps.activities.services import record

from . import custom_fields
from .models import Company, Contact, DuplicateStrategy, Entity, ImportJob, ImportStatus, LifecycleStage, Source, Tag

User = get_user_model()

MAX_STORED_ERRORS = 200
PROGRESS_EVERY = 25

STANDARD_TARGETS = [
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("job_title", "Job title"),
    ("company", "Company name"),
    ("lifecycle_stage", "Lifecycle stage"),
    ("owner", "Owner (email)"),
    ("tags", "Tags (comma separated)"),
]


def import_targets(org):
    return STANDARD_TARGETS + [
        (f"cf:{d.key}", f"{d.label} (custom)") for d in custom_fields.definitions_for(org, Entity.CONTACT)
    ]


def read_headers(uploaded_file):
    """Returns (headers, row_count) and rewinds the file. Raises ValueError for unreadable files."""
    text = _decode(uploaded_file.read())
    uploaded_file.seek(0)
    reader = csv.reader(io.StringIO(text))
    headers = next(reader, None)
    if not headers or not any(h.strip() for h in headers):
        raise ValueError("The file has no header row.")
    rows = sum(1 for row in reader if any(cell.strip() for cell in row))
    if rows == 0:
        raise ValueError("The file has headers but no data rows.")
    return [h.strip() for h in headers], rows


def _decode(raw):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Couldn't read the file. Save it as CSV (UTF-8) and try again.")


class RowError(Exception):
    pass


class ContactImporter:
    def __init__(self, job):
        self.job = job
        self.org = job.org
        self.mapping = job.mapping
        self.defs = {d.key: d for d in custom_fields.definitions_for(self.org, Entity.CONTACT)}
        self.extra_tags = list(job.add_tags.all())
        self.tag_cache = {t.name.lower(): t for t in Tag.objects.for_org(self.org)}
        self.company_cache = {}
        self.owner_cache = {u.email: u for u in User.objects.filter(memberships__org=self.org, memberships__is_active=True)}
        self.stage_lookup = {v.lower(): v for v in LifecycleStage.values} | {
            label.lower(): value for value, label in LifecycleStage.choices
        }

    def run(self):
        job = self.job
        job.status = ImportStatus.RUNNING
        job.save(update_fields=["status", "updated_at"])
        with job.file.open("rb") as handle:
            reader = csv.DictReader(io.StringIO(_decode(handle.read())))
            reader.fieldnames = [h.strip() for h in reader.fieldnames or []]
            for row in reader:
                # reader.line_num counts blank lines too, so errors point at the line people see in Excel.
                line_number = reader.line_num
                if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
                    continue
                try:
                    with transaction.atomic():
                        outcome = self.import_row(row)
                    setattr(job, f"{outcome}_count", getattr(job, f"{outcome}_count") + 1)
                except (RowError, ValueError, IntegrityError) as exc:
                    job.error_count += 1
                    if len(job.errors) < MAX_STORED_ERRORS:
                        job.errors.append({"row": line_number, "message": str(exc)})
                job.processed_rows += 1
                if job.processed_rows % PROGRESS_EVERY == 0:
                    job.save(update_fields=self._counter_fields())
        job.status = ImportStatus.DONE
        job.finished_at = timezone.now()
        job.save(update_fields=self._counter_fields() + ["status", "finished_at"])
        return job

    def _counter_fields(self):
        return ["processed_rows", "created_count", "updated_count", "skipped_count", "error_count", "errors", "updated_at"]

    def import_row(self, row):
        values = {target: (row.get(header) or "").strip() for header, target in self.mapping.items() if target != "tags"}
        tag_cells = [row.get(header) or "" for header, target in self.mapping.items() if target == "tags"]

        email = values.get("email", "").lower()
        if email:
            try:
                validate_email(email)
            except ValidationError:
                raise RowError(f"'{email}' is not a valid email") from None
        if not any(values.get(f) for f in ("first_name", "last_name", "email", "phone")):
            raise RowError("Row has no name, email or phone")

        existing = Contact.objects.for_org(self.org).filter(email=email).first() if email else None
        if existing and self.job.duplicate_strategy == DuplicateStrategy.SKIP:
            return "skipped"

        contact = existing or Contact(org=self.org, source=Source.IMPORT, created_by=self.job.created_by)
        for field in ("first_name", "last_name", "phone", "job_title"):
            if values.get(field):
                setattr(contact, field, values[field][: Contact._meta.get_field(field).max_length])
        if email:
            contact.email = email
        if values.get("lifecycle_stage"):
            stage = self.stage_lookup.get(values["lifecycle_stage"].lower())
            if not stage:
                raise RowError(f"Unknown lifecycle stage '{values['lifecycle_stage']}'")
            contact.lifecycle_stage = stage
        if values.get("company"):
            contact.company = self.company(values["company"])
        owner_email = values.get("owner", "").lower()
        if owner_email:
            if owner_email not in self.owner_cache:
                raise RowError(f"Owner '{owner_email}' is not a member of this workspace")
            contact.owner = self.owner_cache[owner_email]
        elif not contact.owner_id and self.job.default_owner_id:
            contact.owner_id = self.job.default_owner_id

        custom = dict(contact.custom_fields or {})
        for target, raw in values.items():
            if target.startswith("cf:") and (definition := self.defs.get(target[3:])):
                value = custom_fields.coerce(definition, raw)
                if value is not None:
                    custom[definition.key] = value
        contact.custom_fields = custom
        contact.save()

        tags = {self.tag(name) for cell in tag_cells for name in cell.split(",") if name.strip()}
        if tags or self.extra_tags:
            contact.tags.add(*tags, *self.extra_tags)

        verb = "Updated from" if existing else "Imported from"
        record(org=self.org, kind=ActivityKind.IMPORTED, actor=self.job.created_by, contact=contact,
               summary=f"{verb} {self.job.original_name}", data={"import_job": self.job.pk})
        return "updated" if existing else "created"

    def company(self, name):
        key = name.lower()
        if key not in self.company_cache:
            self.company_cache[key] = Company.objects.for_org(self.org).filter(name__iexact=name).first() or (
                Company.objects.create(org=self.org, name=name[:200], created_by=self.job.created_by)
            )
        return self.company_cache[key]

    def tag(self, name):
        name = name.strip()[:50]
        if name.lower() not in self.tag_cache:
            self.tag_cache[name.lower()] = Tag.objects.create(org=self.org, name=name)
        return self.tag_cache[name.lower()]


def run_import(job_id):
    job = ImportJob.objects.select_related("org").get(pk=job_id)
    try:
        return ContactImporter(job).run()
    except Exception as exc:
        ImportJob.objects.filter(pk=job.pk).update(
            status=ImportStatus.FAILED, finished_at=timezone.now(), errors=[{"row": None, "message": str(exc)}]
        )
        raise


# --- Export -----------------------------------------------------------------------------------------------------


class Echo:
    """File-like object for StreamingHttpResponse + csv.writer."""

    def write(self, value):
        return value


def contact_rows(queryset, org):
    defs = custom_fields.definitions_for(org, Entity.CONTACT)
    yield ["First name", "Last name", "Email", "Phone", "Job title", "Company", "Lifecycle stage", "Owner", "Tags",
           "Source", "Created"] + [d.label for d in defs]
    stages = dict(LifecycleStage.choices)
    sources = dict(Source.choices)
    for c in queryset.select_related("company", "owner").prefetch_related("tags").iterator(chunk_size=500):
        yield [
            c.first_name, c.last_name, c.email, c.phone, c.job_title, c.company.name if c.company else "",
            stages.get(c.lifecycle_stage, c.lifecycle_stage), c.owner.email if c.owner else "",
            ", ".join(t.name for t in c.tags.all()), sources.get(c.source, c.source), c.created_at.date().isoformat(),
        ] + [_export_value(c.custom_fields.get(d.key)) for d in defs]


def company_rows(queryset, org):
    defs = custom_fields.definitions_for(org, Entity.COMPANY)
    yield ["Name", "Domain", "Industry", "Phone", "Website", "Address", "City", "Country", "Owner", "Tags", "Contacts",
           "Created"] + [d.label for d in defs]
    for c in queryset.select_related("owner").prefetch_related("tags").iterator(chunk_size=500):
        yield [
            c.name, c.domain, c.industry, c.phone, c.website, c.address, c.city, c.country,
            c.owner.email if c.owner else "", ", ".join(t.name for t in c.tags.all()), c.contact_count,
            c.created_at.date().isoformat(),
        ] + [_export_value(c.custom_fields.get(d.key)) for d in defs]


def _export_value(value):
    if value is True:
        return "yes"
    if value is None or value is False:
        return ""
    return value


def csv_stream(rows):
    writer = csv.writer(Echo())
    yield "﻿"  # BOM so Excel opens UTF-8 (Bangla names etc.) correctly
    for row in rows:
        yield writer.writerow([_safe_cell(v) for v in row])


def _safe_cell(value):
    """Blocks CSV formula injection when the file is opened in Excel/Sheets."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
