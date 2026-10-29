import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.contacts.tests.base import CrmTestCase
from apps.core.views import backup_status
from apps.orgs.models import Membership, Organization, Role
from apps.pipeline.models import Deal

DEMO_ENV = {"DEMO_EMAIL": "Demo@Example.test", "DEMO_PASSWORD": "demo-pass-123"}


@override_settings(DEBUG=True)
class SeedDemoTests(TestCase):
    def test_creates_workspace_logins_and_data_once(self):
        with mock.patch.dict(os.environ, DEMO_ENV):
            call_command("seed_demo", stdout=open(os.devnull, "w"))
            call_command("seed_demo", stdout=open(os.devnull, "w"))
        org = Organization.objects.get(name="Demo Traders")
        roles = dict(Membership.objects.filter(org=org).values_list("user__email", "role"))
        self.assertEqual(roles, {"demo@example.test": Role.ADMIN, "manager@example.test": Role.MANAGER, "agent@example.test": Role.AGENT})
        self.assertTrue(User.objects.get(email="agent@example.test").check_password("demo-pass-123"))
        self.assertEqual(Deal.objects.filter(org=org).count(), 7)
        self.assertEqual(Organization.objects.filter(name="Demo Traders").count(), 1)

    def test_output_never_shows_the_password(self):
        from io import StringIO

        out = StringIO()
        with mock.patch.dict(os.environ, DEMO_ENV):
            call_command("seed_demo", stdout=out)
        self.assertNotIn("demo-pass-123", out.getvalue())

    def test_refuses_without_credentials_or_in_production(self):
        with mock.patch.dict(os.environ, {"DEMO_EMAIL": "", "DEMO_PASSWORD": ""}):
            with self.assertRaisesMessage(CommandError, "DEMO_EMAIL"):
                call_command("seed_demo")
        with override_settings(DEBUG=False), mock.patch.dict(os.environ, DEMO_ENV):
            with self.assertRaisesMessage(CommandError, "production"):
                call_command("seed_demo")


@override_settings(LOGIN_MAX_FAILURES=3, LOGIN_LOCKOUT_MINUTES=15)
class LoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email="rahim@example.com", password="right-pass-123")
        self.url = reverse("accounts:login")

    def tearDown(self):
        cache.clear()

    def login(self, password, email="rahim@example.com", ip="203.0.113.5"):
        return self.client.post(self.url, {"username": email, "password": password}, REMOTE_ADDR=ip)

    def test_locks_after_repeated_failures_even_with_the_right_password(self):
        for _ in range(3):
            self.assertEqual(self.login("wrong").status_code, 200)
        locked = self.login("right-pass-123")
        self.assertContains(locked, "Too many failed attempts")
        self.assertNotIn("_auth_user_id", self.client.session)
        # Another IP isn't affected.
        self.assertEqual(self.login("right-pass-123", ip="198.51.100.7").status_code, 302)

    def test_success_clears_the_counter(self):
        self.login("wrong")
        self.login("wrong")
        self.assertEqual(self.login("right-pass-123").status_code, 302)
        self.client.logout()
        self.login("wrong")
        self.login("wrong")
        self.assertEqual(self.login("right-pass-123").status_code, 302)

    def test_password_reset_emails_are_limited_per_ip(self):
        from django.core import mail

        for _ in range(7):
            self.client.post(reverse("accounts:password_reset"), {"email": "rahim@example.com"}, REMOTE_ADDR="203.0.113.9")
        self.assertEqual(len(mail.outbox), 5)


class ErrorPageAndAssetTests(CrmTestCase):
    def test_branded_404(self):
        response = self.client.get("/definitely-not-a-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)

    def test_assets_switch_between_cdn_and_vendored(self):
        template = Template("{% load assets %}{% asset 'jquery' %}|{% tailwind %}")
        with override_settings(COMPILED_ASSETS=False):
            dev = template.render(Context())
        self.assertIn("https://code.jquery.com/jquery-3.7.1.min.js", dev)
        self.assertIn('type="text/tailwindcss"', dev)
        self.assertIn(".btn-primary", dev)
        self.assertNotIn("@import", dev)
        with override_settings(COMPILED_ASSETS=True):
            prod = template.render(Context())
        self.assertIn("/static/vendor/jquery.min.js", prod)
        self.assertIn('href="/static/css/app.css"', prod)
        self.assertNotIn("tailwindcss/browser", prod)

    def test_every_library_file_is_listed_with_a_pinned_version(self):
        from apps.core.assets import LIBRARIES

        for name, lib in LIBRARIES.items():
            self.assertRegex(lib["package"], r"@\d", name)
            self.assertIn(lib["package"].rsplit("@", 1)[1], lib["cdn"], name)


class BackupStatusTests(CrmTestCase):
    def test_not_configured(self):
        with override_settings(BACKUP_DIR=""):
            self.assertIsNone(backup_status())

    def test_latest_backup_and_staleness(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(BACKUP_DIR=folder, BACKUP_STALE_HOURS=36):
            self.assertTrue(backup_status()["stale"])  # empty folder
            old = Path(folder, "crm-20261001-020000.dump")
            old.write_bytes(b"x" * 10)
            past = (timezone.now() - timedelta(days=3)).timestamp()
            os.utime(old, (past, past))
            self.assertTrue(backup_status()["stale"])
            Path(folder, "crm-20261029-020000.dump").write_bytes(b"y" * 2048)
            status = backup_status()
            self.assertEqual((status["latest"], status["count"], status["stale"]), ("crm-20261029-020000.dump", 2, False))
            self.login(self.admin)
            self.assertContains(self.client.get(reverse("core:system")), "Up to date")
