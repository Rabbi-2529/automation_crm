import re

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import OrgScopedModel


def phone_digits(phone):
    """Digits only, used for duplicate detection and search (`+880 1711-000000` -> `8801711000000`)."""
    return re.sub(r"\D", "", phone or "")


class TagColor(models.TextChoices):
    SLATE = "slate", "Grey"
    RED = "red", "Red"
    AMBER = "amber", "Amber"
    EMERALD = "emerald", "Green"
    SKY = "sky", "Blue"
    INDIGO = "indigo", "Indigo"
    VIOLET = "violet", "Violet"
    PINK = "pink", "Pink"


class Tag(OrgScopedModel):
    name = models.CharField(max_length=50)
    color = models.CharField(max_length=16, choices=TagColor.choices, default=TagColor.SLATE)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["org", "name"], name="unique_tag_name_per_org")]

    def __str__(self):
        return self.name


class Entity(models.TextChoices):
    CONTACT = "contact", "Contact"
    COMPANY = "company", "Company"
    DEAL = "deal", "Deal"


class FieldType(models.TextChoices):
    TEXT = "text", "Text"
    NUMBER = "number", "Number"
    DATE = "date", "Date"
    SELECT = "select", "Dropdown"
    CHECKBOX = "checkbox", "Yes / no"
    URL = "url", "URL"
    EMAIL = "email", "Email"


class CustomFieldDefinition(OrgScopedModel):
    """An org-defined extra field. Values live in the owning record's `custom_fields` JSON under `key`."""

    entity = models.CharField(max_length=16, choices=Entity.choices)
    key = models.SlugField(max_length=50, help_text="Used in imports and automations. Can't be changed later.")
    label = models.CharField(max_length=80)
    field_type = models.CharField(max_length=16, choices=FieldType.choices, default=FieldType.TEXT)
    options = models.JSONField(default=list, blank=True, help_text="Choices for dropdown fields.")
    required = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["entity", "position", "label"]
        constraints = [models.UniqueConstraint(fields=["org", "entity", "key"], name="unique_custom_field_key")]

    def __str__(self):
        return f"{self.get_entity_display()}: {self.label}"


class CustomFieldsMixin(models.Model):
    custom_fields = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True


class Company(OrgScopedModel, CustomFieldsMixin):
    name = models.CharField(max_length=200)
    domain = models.CharField(max_length=200, blank=True)
    industry = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    website = models.URLField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tags = models.ManyToManyField(Tag, blank=True, related_name="companies")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "companies"
        indexes = [models.Index(fields=["org", "name"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.domain = self.domain.strip().lower().removeprefix("http://").removeprefix("https://").removeprefix("www.").rstrip("/")
        super().save(*args, **kwargs)


class LifecycleStage(models.TextChoices):
    LEAD = "lead", "Lead"
    QUALIFIED = "qualified", "Qualified"
    OPPORTUNITY = "opportunity", "Opportunity"
    CUSTOMER = "customer", "Customer"
    CHURNED = "churned", "Churned"
    OTHER = "other", "Other"


class Source(models.TextChoices):
    MANUAL = "manual", "Added manually"
    IMPORT = "import", "CSV import"
    WEB_FORM = "web_form", "Web form"
    API = "api", "API"
    OTHER = "other", "Other"


class Contact(OrgScopedModel, CustomFieldsMixin):
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    phone_digits = models.CharField(max_length=32, blank=True, editable=False, db_index=True)
    job_title = models.CharField(max_length=120, blank=True)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="contacts")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    lifecycle_stage = models.CharField(max_length=16, choices=LifecycleStage.choices, default=LifecycleStage.LEAD)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    tags = models.ManyToManyField(Tag, blank=True, related_name="contacts")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["first_name", "last_name", "email"]
        constraints = [
            # One contact per email inside an org; blank emails are allowed many times.
            models.UniqueConstraint(fields=["org", "email"], condition=~Q(email=""), name="unique_contact_email_per_org"),
        ]
        indexes = [
            models.Index(fields=["org", "email"]),
            models.Index(fields=["org", "lifecycle_stage"]),
            models.Index(fields=["org", "-created_at"]),
        ]

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        self.phone = self.phone.strip()
        self.phone_digits = phone_digits(self.phone)
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def display_name(self):
        return self.full_name or self.email or self.phone or f"Contact #{self.pk}"

    @property
    def initials(self):
        letters = (self.first_name[:1] + self.last_name[:1]) or self.display_name[:2]
        return letters.upper()


class ImportStatus(models.TextChoices):
    MAPPING = "mapping", "Waiting for column mapping"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    DONE = "done", "Finished"
    FAILED = "failed", "Failed"


class DuplicateStrategy(models.TextChoices):
    SKIP = "skip", "Skip rows whose email already exists"
    UPDATE = "update", "Update the existing contact"


def import_upload_path(instance, filename):
    return f"org_{instance.org_id}/imports/{filename}"


class ImportJob(OrgScopedModel):
    file = models.FileField(upload_to=import_upload_path)
    original_name = models.CharField(max_length=255)
    headers = models.JSONField(default=list)
    mapping = models.JSONField(default=dict, blank=True, help_text="CSV header -> target field")
    duplicate_strategy = models.CharField(max_length=8, choices=DuplicateStrategy.choices, default=DuplicateStrategy.SKIP)
    default_owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    add_tags = models.ManyToManyField(Tag, blank=True, related_name="+")
    status = models.CharField(max_length=16, choices=ImportStatus.choices, default=ImportStatus.MAPPING)
    total_rows = models.PositiveIntegerField(default=0)
    processed_rows = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True, help_text="First 200 row errors: [{row, message}]")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Import {self.original_name} ({self.get_status_display()})"

    @property
    def is_active(self):
        return self.status in (ImportStatus.QUEUED, ImportStatus.RUNNING)

    @property
    def percent(self):
        return int(self.processed_rows * 100 / self.total_rows) if self.total_rows else 0
