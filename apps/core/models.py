from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class OrgScopedQuerySet(models.QuerySet):
    def for_org(self, org):
        return self.filter(org=org)


class OrgScopedModel(TimeStampedModel):
    """Base for every tenant-owned record. Always query through `.for_org(request.org)`."""

    org = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="+")

    objects = OrgScopedQuerySet.as_manager()

    class Meta:
        abstract = True
