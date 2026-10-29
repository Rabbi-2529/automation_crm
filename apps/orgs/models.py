from django.conf import settings
from django.db import models
from django.utils.text import slugify

from apps.core.models import OrgScopedModel, TimeStampedModel


class Role(models.TextChoices):
    ADMIN = "admin", "Admin"
    MANAGER = "manager", "Manager"
    AGENT = "agent", "Agent"


# Higher number = more access, so a MANAGER check also passes for ADMIN.
ROLE_RANK = {Role.AGENT: 10, Role.MANAGER: 20, Role.ADMIN: 30}


class Organization(TimeStampedModel):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160, unique=True)
    timezone = models.CharField(max_length=64, default="Asia/Dhaka")
    currency = models.CharField(max_length=3, default="BDT")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self):
        base = slugify(self.name)[:150] or "org"
        slug, n = base, 2
        while Organization.objects.filter(slug=slug).exists():
            slug, n = f"{base}-{n}", n + 1
        return slug


class Membership(TimeStampedModel):
    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.AGENT)
    is_active = models.BooleanField(default=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["org", "user__email"]
        constraints = [models.UniqueConstraint(fields=["org", "user"], name="unique_membership")]

    def __str__(self):
        return f"{self.user} @ {self.org} ({self.role})"

    def has_role(self, min_role):
        return self.is_active and ROLE_RANK[self.role] >= ROLE_RANK[min_role]

    @property
    def is_admin(self):
        return self.role == Role.ADMIN

    @property
    def is_manager(self):
        return self.has_role(Role.MANAGER)


class Team(OrgScopedModel):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    lead = models.ForeignKey(Membership, null=True, blank=True, on_delete=models.SET_NULL, related_name="led_teams")
    members = models.ManyToManyField(Membership, blank=True, related_name="teams")

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["org", "name"], name="unique_team_name_per_org")]

    def __str__(self):
        return self.name
