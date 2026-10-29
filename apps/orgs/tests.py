from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.core.testing import make_member, make_org, make_user

from .middleware import SESSION_KEY
from .models import Membership, Role, Team

User = get_user_model()


class OrgTestCase(TestCase):
    def setUp(self):
        self.org = make_org("Acme")
        self.admin = make_member(self.org, Role.ADMIN)
        self.manager = make_member(self.org, Role.MANAGER)
        self.agent = make_member(self.org, Role.AGENT)

    def login(self, membership):
        self.client.force_login(membership.user)


class CurrentOrganizationTests(OrgTestCase):
    def test_user_without_org_is_sent_to_create_workspace(self):
        self.client.force_login(make_user())
        self.assertRedirects(self.client.get(reverse("core:dashboard")), reverse("orgs:create"))

    def test_switch_to_own_org(self):
        other = make_org("Beta")
        make_member(other, Role.AGENT, user=self.agent.user)
        self.login(self.agent)
        self.client.post(reverse("orgs:switch", args=[other.pk]))
        self.assertEqual(self.client.session[SESSION_KEY], other.pk)
        self.assertContains(self.client.get(reverse("core:dashboard")), "Beta")

    def test_cannot_switch_to_foreign_org(self):
        foreign = make_org("Foreign")
        self.login(self.agent)
        self.assertEqual(self.client.post(reverse("orgs:switch", args=[foreign.pk])).status_code, 404)

    def test_deactivated_membership_loses_access(self):
        self.agent.is_active = False
        self.agent.save()
        self.login(self.agent)
        self.assertRedirects(self.client.get(reverse("core:dashboard")), reverse("orgs:create"))

    def test_create_second_workspace_makes_user_admin_of_it(self):
        self.login(self.agent)
        self.client.post(reverse("orgs:create"), {"name": "Side Biz", "timezone": "Asia/Dhaka", "currency": "usd"})
        membership = Membership.objects.get(user=self.agent.user, org__name="Side Biz")
        self.assertEqual(membership.role, Role.ADMIN)
        self.assertEqual(membership.org.currency, "USD")
        self.assertEqual(self.client.session[SESSION_KEY], membership.org_id)


class RolePermissionTests(OrgTestCase):
    def test_role_gates(self):
        cases = [
            # (url, agent, manager, admin)
            (reverse("orgs:teams"), 200, 200, 200),
            (reverse("orgs:team_create"), 403, 200, 200),
            (reverse("orgs:members"), 403, 200, 200),
            (reverse("orgs:member_invite"), 403, 403, 200),
            (reverse("orgs:settings"), 403, 403, 200),
            (reverse("core:system"), 403, 403, 200),
        ]
        for url, *expected in cases:
            for membership, status in zip([self.agent, self.manager, self.admin], expected):
                with self.subTest(url=url, role=membership.role):
                    self.login(membership)
                    self.assertEqual(self.client.get(url).status_code, status)


class InviteTests(OrgTestCase):
    def invite(self, data):
        # The email goes out on transaction commit, which TestCase never reaches on its own.
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("orgs:member_invite"), data)

    def test_invite_new_user_sends_set_password_link(self):
        self.login(self.admin)
        response = self.invite({"email": "New@Example.com", "first_name": "Nadia", "role": Role.MANAGER})
        self.assertRedirects(response, reverse("orgs:members"))
        user = User.objects.get(email="new@example.com")
        self.assertFalse(user.has_usable_password())
        self.assertEqual(Membership.objects.get(user=user, org=self.org).role, Role.MANAGER)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/password/reset/", mail.outbox[0].body)

    def test_invited_user_can_set_password_from_link(self):
        self.login(self.admin)
        self.invite({"email": "new@example.com", "role": Role.AGENT})
        link = next(line for line in mail.outbox[0].body.splitlines() if "/password/reset/" in line)
        self.client.logout()
        response = self.client.get(link, follow=True)  # Django swaps the token for a session marker
        self.client.post(response.redirect_chain[-1][0], {"new_password1": "fresh-pass-987", "new_password2": "fresh-pass-987"})
        self.assertTrue(self.client.login(username="new@example.com", password="fresh-pass-987"))

    def test_invite_existing_user_adds_membership_without_new_account(self):
        outsider = make_user(email="known@example.com")
        self.login(self.admin)
        self.invite({"email": "known@example.com", "role": Role.AGENT})
        self.assertTrue(Membership.objects.filter(user=outsider, org=self.org).exists())
        self.assertEqual(User.objects.filter(email="known@example.com").count(), 1)
        self.assertIn("Log in", mail.outbox[0].body)

    def test_reinvite_reactivates_membership(self):
        self.agent.is_active = False
        self.agent.save()
        self.login(self.admin)
        self.invite({"email": self.agent.user.email, "role": Role.MANAGER})
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.is_active)
        self.assertEqual(self.agent.role, Role.MANAGER)

    def test_cannot_invite_active_member_twice(self):
        self.login(self.admin)
        response = self.invite({"email": self.agent.user.email, "role": Role.AGENT})
        self.assertContains(response, "already a member")


class MemberEditTests(OrgTestCase):
    def test_last_admin_cannot_demote_themselves(self):
        self.login(self.admin)
        response = self.client.post(reverse("orgs:member_edit", args=[self.admin.pk]), {"role": Role.AGENT, "is_active": "on"})
        self.assertContains(response, "at least one active admin")
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, Role.ADMIN)

    def test_admin_can_be_demoted_when_another_admin_exists(self):
        second = make_member(self.org, Role.ADMIN)
        self.login(self.admin)
        self.client.post(reverse("orgs:member_edit", args=[second.pk]), {"role": Role.MANAGER, "is_active": "on"})
        second.refresh_from_db()
        self.assertEqual(second.role, Role.MANAGER)

    def test_cannot_edit_member_of_another_org(self):
        foreign = make_member(make_org("Foreign"), Role.AGENT)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse("orgs:member_edit", args=[foreign.pk])).status_code, 404)


class TeamTests(OrgTestCase):
    def test_manager_creates_team_with_org_members(self):
        self.login(self.manager)
        response = self.client.post(
            reverse("orgs:team_create"),
            {"name": "Inside Sales", "lead": self.manager.pk, "members": [self.manager.pk, self.agent.pk]},
        )
        self.assertRedirects(response, reverse("orgs:teams"))
        team = Team.objects.get()
        self.assertEqual(team.org, self.org)
        self.assertEqual(set(team.members.all()), {self.manager, self.agent})

    def test_members_from_other_orgs_are_rejected(self):
        foreign = make_member(make_org("Foreign"), Role.AGENT)
        self.login(self.manager)
        response = self.client.post(reverse("orgs:team_create"), {"name": "Sneaky", "members": [foreign.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Team.objects.exists())

    def test_duplicate_team_name_in_same_org(self):
        Team.objects.create(org=self.org, name="Sales")
        self.login(self.manager)
        response = self.client.post(reverse("orgs:team_create"), {"name": "sales"})
        self.assertContains(response, "already exists")

    def test_teams_are_isolated_per_org(self):
        foreign_team = Team.objects.create(org=make_org("Foreign"), name="Theirs")
        self.login(self.admin)
        self.assertNotContains(self.client.get(reverse("orgs:teams")), "Theirs")
        self.assertEqual(self.client.get(reverse("orgs:team_edit", args=[foreign_team.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("orgs:team_delete", args=[foreign_team.pk])).status_code, 404)

    def test_delete_team(self):
        team = Team.objects.create(org=self.org, name="Sales")
        self.login(self.manager)
        self.client.post(reverse("orgs:team_delete", args=[team.pk]))
        self.assertFalse(Team.objects.exists())
