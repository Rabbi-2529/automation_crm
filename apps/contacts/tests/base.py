from django.test import TestCase

from apps.core.testing import make_member, make_org
from apps.orgs.models import Role

from ..models import Company, Contact, Tag


class CrmTestCase(TestCase):
    """An org with an admin, manager and agent, plus a foreign org to check isolation against."""

    def setUp(self):
        self.org = make_org("ThingBD")
        self.admin = make_member(self.org, Role.ADMIN)
        self.manager = make_member(self.org, Role.MANAGER)
        self.agent = make_member(self.org, Role.AGENT)
        self.other_org = make_org("Rival")
        self.outsider = make_member(self.other_org, Role.ADMIN)

    def login(self, membership):
        self.client.force_login(membership.user)

    def contact(self, org=None, **fields):
        return Contact.objects.create(org=org or self.org, **fields)

    def company(self, name="Acme", org=None, **fields):
        return Company.objects.create(org=org or self.org, name=name, **fields)

    def tag(self, name="VIP", org=None, **fields):
        return Tag.objects.create(org=org or self.org, name=name, **fields)
