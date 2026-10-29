"""Shared factories for tests across apps."""

from itertools import count

from apps.accounts.models import User
from apps.orgs.models import Membership, Organization, Role

_seq = count(1)


def make_user(email=None, password="pass-12345", **fields):
    email = email or f"user{next(_seq)}@example.com"
    return User.objects.create_user(email=email, password=password, **fields)


def make_org(name=None, **fields):
    return Organization.objects.create(name=name or f"Org {next(_seq)}", **fields)


def make_member(org, role=Role.AGENT, user=None, **fields):
    return Membership.objects.create(org=org, user=user or make_user(), role=role, **fields)
