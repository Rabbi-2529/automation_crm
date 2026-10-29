from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.contacts.models import CustomFieldsMixin
from apps.core.models import OrgScopedModel


class Pipeline(OrgScopedModel):
    name = models.CharField(max_length=100)
    is_default = models.BooleanField(default=False, help_text="Opened first on the deals board.")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "name"]
        constraints = [models.UniqueConstraint(fields=["org", "name"], name="unique_pipeline_name_per_org")]

    def __str__(self):
        return self.name


class Stage(OrgScopedModel):
    """An open step in a pipeline. Won/lost is a deal status, not a stage, so closed deals keep their last stage."""

    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name="stages")
    name = models.CharField(max_length=60)
    position = models.PositiveSmallIntegerField(default=0)
    probability = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Chance (%) a deal in this stage closes as won. Used for the weighted forecast.",
    )
    rot_days = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Flag deals with no activity for this many days. Leave empty to never flag.",
    )

    class Meta:
        ordering = ["pipeline", "position", "pk"]

    def __str__(self):
        return self.name


class LostReason(OrgScopedModel):
    name = models.CharField(max_length=100)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "name"]
        constraints = [models.UniqueConstraint(fields=["org", "name"], name="unique_lost_reason_per_org")]

    def __str__(self):
        return self.name


class DealStatus(models.TextChoices):
    OPEN = "open", "Open"
    WON = "won", "Won"
    LOST = "lost", "Lost"


class Deal(OrgScopedModel, CustomFieldsMixin):
    title = models.CharField(max_length=200)
    value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, blank=True, help_text="Defaults to the workspace currency.")
    pipeline = models.ForeignKey(Pipeline, on_delete=models.PROTECT, related_name="deals")
    stage = models.ForeignKey(Stage, on_delete=models.PROTECT, related_name="deals")
    status = models.CharField(max_length=8, choices=DealStatus.choices, default=DealStatus.OPEN)
    contact = models.ForeignKey("contacts.Contact", null=True, blank=True, on_delete=models.SET_NULL, related_name="deals")
    company = models.ForeignKey("contacts.Company", null=True, blank=True, on_delete=models.SET_NULL, related_name="deals")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tags = models.ManyToManyField("contacts.Tag", blank=True, related_name="deals")
    expected_close_date = models.DateField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.ForeignKey(LostReason, null=True, blank=True, on_delete=models.SET_NULL, related_name="deals")
    lost_note = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0, help_text="Order inside its kanban column.")
    stage_changed_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["position", "-created_at"]
        indexes = [
            models.Index(fields=["org", "pipeline", "status"]),
            models.Index(fields=["stage", "status", "position"]),
            models.Index(fields=["org", "status", "closed_at"]),
        ]

    def __str__(self):
        return self.title

    @property
    def display_name(self):
        return self.title

    @property
    def currency_code(self):
        return self.currency or self.org.currency

    @property
    def weighted_value(self):
        return self.value * self.stage.probability / 100

    @property
    def is_rotting(self):
        if self.status != DealStatus.OPEN or not self.stage.rot_days:
            return False
        last_touch = max(filter(None, [self.last_activity_at, self.stage_changed_at]))
        return timezone.now() - last_touch > timedelta(days=self.stage.rot_days)

    @property
    def is_overdue(self):
        return self.status == DealStatus.OPEN and self.expected_close_date and self.expected_close_date < timezone.localdate()
