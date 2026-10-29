from django.test import TestCase
from django.urls import reverse

from apps.orgs.models import Role

from .testing import make_member, make_org


class DashboardTests(TestCase):
    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next=/")

    def test_member_sees_dashboard_and_org_stats(self):
        org = make_org("Acme")
        membership = make_member(org, Role.AGENT)
        make_member(org, Role.AGENT)
        self.client.force_login(membership.user)
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, "Acme")
        self.assertEqual(response.context["member_count"], 2)


class SystemTests(TestCase):
    def test_health_check_is_public(self):
        response = self.client.get(reverse("core:health"))
        self.assertEqual(response.json(), {"status": "ok", "database": True})

    def test_ping_task_round_trip(self):
        admin = make_member(make_org(), Role.ADMIN)
        self.client.force_login(admin.user)
        response = self.client.post(reverse("core:system_ping"))
        self.assertContains(response, "Worker answered")
