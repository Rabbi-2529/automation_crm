"""Facebook / Instagram lead ads: Meta's leadgen webhook names a lead; we fetch its answers and create or update the contact."""

import logging
from datetime import timedelta

import requests
from django.db import transaction
from django.utils import timezone

from apps.activities.models import ActivityKind, Note
from apps.activities.services import record
from apps.contacts.models import Company, Contact, CustomFieldDefinition, Entity, Source
from apps.messaging.whatsapp import GRAPH_URL

from .models import FacebookLead, FacebookLeadSettings, FacebookPage, LeadStatus

logger = logging.getLogger(__name__)
TIMEOUT = 15
MAX_ATTEMPTS = 5

# Meta's standard question keys -> contact fields.
FIELD_MAP = {
    "email": "email", "work_email": "email",
    "phone_number": "phone", "phone": "phone", "work_phone_number": "phone",
    "first_name": "first_name", "last_name": "last_name",
    "job_title": "job_title", "company_name": "company",
}


class LeadFetchError(Exception):
    pass


def parse_webhook(payload):
    """leadgen changes in a Page webhook body -> [{"leadgen_id", "page_id", "form_id", "ad_id"}]."""
    leads = []
    for entry in (payload or {}).get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "leadgen":
                continue
            value = change.get("value") or {}
            if value.get("leadgen_id"):
                leads.append({"leadgen_id": str(value["leadgen_id"]), "page_id": str(value.get("page_id") or entry.get("id", "")),
                              "form_id": str(value.get("form_id", "")), "ad_id": str(value.get("ad_id", ""))})
    return leads


def receive(settings_obj, payload):
    """Stores each new lead from a verified webhook and queues its import. Returns how many were new."""
    from .tasks import import_facebook_lead

    new = 0
    for item in parse_webhook(payload):
        page = FacebookPage.objects.filter(org=settings_obj.org, page_id=item["page_id"], is_active=True).first()
        if page is None:
            logger.info("Facebook lead for unknown page %s in org %s", item["page_id"], settings_obj.org_id)
            continue
        lead, created = FacebookLead.objects.get_or_create(
            leadgen_id=item["leadgen_id"],
            defaults={"org": settings_obj.org, "page": page, "form_id": item["form_id"], "ad_id": item["ad_id"]})
        if created:
            new += 1
            transaction.on_commit(lambda pk=lead.pk: import_facebook_lead.delay(pk))
    return new


def _get(path, token, **params):
    try:
        response = requests.get(f"{GRAPH_URL}/{path}", params={"access_token": token, **params}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise LeadFetchError(f"Facebook unreachable: {exc}") from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        error = data.get("error") or {}
        raise LeadFetchError(f"Facebook error {error.get('code', response.status_code)}: {error.get('message', response.text[:200])}")
    return data


def fetch_answers(lead):
    """{question key: answer} from the Graph API (multi-select answers are joined with commas)."""
    data = _get(lead.leadgen_id, lead.page.access_token, fields="created_time,field_data,form_id,ad_id")
    answers = {}
    for item in data.get("field_data") or []:
        values = [str(v) for v in item.get("values") or [] if v not in (None, "")]
        if item.get("name") and values:
            answers[item["name"]] = ", ".join(values)
    if not lead.form_name and (lead.form_id or data.get("form_id")):
        lead.form_id = lead.form_id or str(data["form_id"])
        known = FacebookLead.objects.filter(form_id=lead.form_id).exclude(form_name="").values_list("form_name", flat=True).first()
        try:
            lead.form_name = known or _get(lead.form_id, lead.page.access_token, fields="name").get("name", "")[:200]
        except LeadFetchError:
            pass  # the form name is only a label
    lead.ad_id = lead.ad_id or str(data.get("ad_id") or "")
    return answers


@transaction.atomic
def import_lead(lead, answers):
    """Creates or updates the contact for a lead's answers and records it on the timeline."""
    org = lead.org
    settings_obj = FacebookLeadSettings.for_org(org)
    values, extra = {}, {}
    for key, value in answers.items():
        field = FIELD_MAP.get(key.lower())
        if field and not values.get(field):
            values[field] = value
        elif key.lower() == "full_name" and not values.get("first_name"):
            first, _, last = value.partition(" ")
            values["first_name"], values["last_name"] = first, values.get("last_name") or last
        else:
            extra[key] = value

    email, phone = values.get("email", "").strip().lower(), values.get("phone", "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    contacts = Contact.objects.for_org(org)
    contact = (contacts.filter(email=email).first() if email else None) or \
              (contacts.filter(phone_digits__endswith=digits[-10:]).first() if len(digits) >= 7 else None)
    created = contact is None
    if created:
        contact = Contact(org=org, source=Source.FACEBOOK, lifecycle_stage=settings_obj.lifecycle_stage or "lead",
                          owner=settings_obj.default_owner)
    for field in ("first_name", "last_name", "email", "phone", "job_title"):
        if values.get(field) and (created or not getattr(contact, field)):
            setattr(contact, field, values[field][:contact._meta.get_field(field).max_length])
    if values.get("company") and not contact.company_id:
        name = values["company"][:200]
        contact.company = Company.objects.for_org(org).filter(name__iexact=name).first() or Company.objects.create(org=org, name=name)
    definitions = {d.key: d for d in CustomFieldDefinition.objects.for_org(org).filter(entity=Entity.CONTACT)}
    custom = dict(contact.custom_fields or {})
    for key in list(extra):
        if key in definitions:
            custom[key] = extra.pop(key)
    contact.custom_fields = custom
    contact.save()
    if settings_obj.add_tags.exists():
        contact.tags.add(*settings_obj.add_tags.all())

    lead.contact, lead.created_contact, lead.field_data = contact, created, answers
    lead.status, lead.error = LeadStatus.IMPORTED, ""
    lead.save()
    FacebookPage.objects.filter(pk=lead.page_id).update(lead_count=lead.page.lead_count + 1, last_lead_at=timezone.now())

    form = lead.form_name or lead.form_id or "lead form"
    if created:
        record(org=org, kind=ActivityKind.CREATED, contact=contact, summary=f"Contact created from Facebook lead ad ({lead.page.name})")
    if extra:
        lines = "\n".join(f"{key.replace('_', ' ').capitalize()}: {value}" for key, value in extra.items())
        note = Note.objects.create(org=org, contact=contact, body=f"Facebook lead form “{form}”:\n{lines}")
        record(org=org, kind=ActivityKind.NOTE_ADDED, contact=contact, note=note, summary="Answers from Facebook lead form")
    record(org=org, kind=ActivityKind.FACEBOOK_LEAD, contact=contact, summary=f"Facebook lead: {form} ({lead.page.name})",
           data={"page": lead.page_id, "form": lead.form_id, "ad": lead.ad_id, "lead": lead.pk})
    return contact


def process(lead):
    """Fetch + import one lead. Failures are kept on the lead (shown in settings) and retried by beat."""
    if lead.status == LeadStatus.IMPORTED:
        return lead
    lead.attempts += 1
    try:
        answers = fetch_answers(lead)
        if not answers:
            raise LeadFetchError("Facebook returned no answers for this lead.")
        import_lead(lead, answers)
    except LeadFetchError as exc:
        lead.status, lead.error = LeadStatus.FAILED, str(exc)[:500]
        lead.save(update_fields=["attempts", "status", "error", "form_id", "form_name", "ad_id", "updated_at"])
    return lead


def retry_failed(now=None):
    now = now or timezone.now()
    failed = FacebookLead.objects.filter(status__in=[LeadStatus.FAILED, LeadStatus.RECEIVED], attempts__lt=MAX_ATTEMPTS,
                                         created_at__gte=now - timedelta(days=2),
                                         updated_at__lte=now - timedelta(minutes=10)).select_related("page", "org")
    return sum(1 for lead in failed[:100] if process(lead).status == LeadStatus.IMPORTED)
