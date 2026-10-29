from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import OrgScopedModel


class TargetMetric(models.TextChoices):
    REVENUE = "revenue", "Revenue won"
    WON_DEALS = "won_deals", "Deals won"
    NEW_DEALS = "new_deals", "New deals"
    NEW_LEADS = "new_leads", "New leads"


class TargetPeriod(models.TextChoices):
    MONTH = "month", "Month"
    QUARTER = "quarter", "Quarter"


class SalesTarget(OrgScopedModel):
    """A goal for one person (or the whole team when user is empty) for a calendar month or quarter."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="+",
                             help_text="Leave empty for a team-wide target.")
    metric = models.CharField(max_length=12, choices=TargetMetric.choices, default=TargetMetric.REVENUE)
    period = models.CharField(max_length=8, choices=TargetPeriod.choices, default=TargetPeriod.MONTH)
    period_start = models.DateField(help_text="First day of the month or quarter.")
    value = models.DecimalField(max_digits=14, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-period_start", "metric"]
        constraints = [
            models.UniqueConstraint(fields=["org", "user", "metric", "period", "period_start"], name="unique_target"),
            models.UniqueConstraint(fields=["org", "metric", "period", "period_start"], condition=Q(user__isnull=True),
                                    name="unique_team_target"),
        ]

    def __str__(self):
        who = self.user.display_name if self.user_id else "Team"
        return f"{who}: {self.get_metric_display()} {self.period_label}"

    @property
    def period_label(self):
        if self.period == TargetPeriod.QUARTER:
            return f"Q{(self.period_start.month - 1) // 3 + 1} {self.period_start.year}"
        return f"{self.period_start:%B %Y}"


class ReportKind(models.TextChoices):
    SUMMARY = "summary", "Team sales summary"
    DIGEST = "digest", "My pipeline digest"


class Frequency(models.TextChoices):
    DAILY = "daily", "Every day"
    WEEKLY = "weekly", "Every week"
    MONTHLY = "monthly", "Every month (on the 1st)"


class ReportSubscription(OrgScopedModel):
    """An email report a member gets on a schedule."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="report_subscriptions")
    kind = models.CharField(max_length=8, choices=ReportKind.choices, default=ReportKind.DIGEST)
    frequency = models.CharField(max_length=8, choices=Frequency.choices, default=Frequency.DAILY)
    weekday = models.PositiveSmallIntegerField(default=6, choices=[(6, "Saturday"), (0, "Sunday"), (1, "Monday"), (2, "Tuesday"),
                                                                   (3, "Wednesday"), (4, "Thursday"), (5, "Friday")],
                                               help_text="For weekly reports.")
    hour = models.PositiveSmallIntegerField(default=9, help_text="Local hour (0-23) to send at.")
    is_active = models.BooleanField(default=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["kind", "frequency"]
        constraints = [models.UniqueConstraint(fields=["org", "user", "kind", "frequency"], name="unique_report_subscription")]

    def __str__(self):
        return f"{self.get_kind_display()} ({self.get_frequency_display()}) for {self.user}"
