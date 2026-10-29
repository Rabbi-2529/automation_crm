from django.test import TestCase
from django.urls import reverse

from apps.core.testing import make_user
from apps.orgs.models import Membership, Role

from .models import User

SIGNUP = {
    "first_name": "Rahim",
    "last_name": "Uddin",
    "email": "Rahim@Example.com",
    "organization_name": "Acme Traders",
    "password1": "a-strong-pass-123",
    "password2": "a-strong-pass-123",
}


class SignupTests(TestCase):
    def test_signup_creates_user_org_and_admin_membership(self):
        response = self.client.post(reverse("accounts:signup"), SIGNUP)

        self.assertRedirects(response, reverse("core:dashboard"))
        user = User.objects.get()
        self.assertEqual(user.email, "rahim@example.com")
        membership = Membership.objects.get(user=user)
        self.assertEqual(membership.role, Role.ADMIN)
        self.assertEqual(membership.org.name, "Acme Traders")
        self.assertEqual(membership.org.slug, "acme-traders")
        self.assertContains(self.client.get(reverse("core:dashboard")), "Acme Traders")

    def test_duplicate_email_is_rejected_case_insensitively(self):
        make_user(email="rahim@example.com")
        response = self.client.post(reverse("accounts:signup"), SIGNUP)
        self.assertContains(response, "already exists")
        self.assertEqual(User.objects.count(), 1)


class LoginTests(TestCase):
    def test_login_with_any_email_case(self):
        make_user(email="karim@example.com", password="pass-12345")
        response = self.client.post(reverse("accounts:login"), {"username": "KARIM@example.com", "password": "pass-12345"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_logout_requires_post(self):
        user = make_user()
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)
