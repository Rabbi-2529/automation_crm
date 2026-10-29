from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import Membership, Organization, Role

User = get_user_model()


@transaction.atomic
def create_organization(owner, name, **fields):
    org = Organization.objects.create(name=name, **fields)
    Membership.objects.create(org=org, user=owner, role=Role.ADMIN)
    return org


def other_active_admins(membership):
    return Membership.objects.filter(org=membership.org, role=Role.ADMIN, is_active=True).exclude(pk=membership.pk)


@transaction.atomic
def invite_member(*, org, invited_by, email, role, first_name="", last_name="", request=None):
    """Adds `email` to `org`. New users get an unusable password and a set-password email."""
    email = email.strip().lower()
    user = User.objects.filter(email=email).first()
    is_new_user = user is None
    if is_new_user:
        user = User(email=email, first_name=first_name, last_name=last_name)
        user.set_unusable_password()
        user.save()

    membership, created = Membership.objects.get_or_create(
        org=org, user=user, defaults={"role": role, "invited_by": invited_by}
    )
    if not created:
        membership.role = role
        membership.is_active = True
        membership.invited_by = invited_by
        membership.save(update_fields=["role", "is_active", "invited_by", "updated_at"])

    transaction.on_commit(lambda: send_invite_email(membership, invited_by, is_new_user, request))
    return membership


def send_invite_email(membership, invited_by, is_new_user, request=None):
    user = membership.user
    if is_new_user:
        path = reverse(
            "accounts:password_reset_confirm",
            kwargs={"uidb64": urlsafe_base64_encode(force_bytes(user.pk)), "token": default_token_generator.make_token(user)},
        )
    else:
        path = reverse("accounts:login")
    link = request.build_absolute_uri(path) if request else path
    context = {"membership": membership, "invited_by": invited_by, "is_new_user": is_new_user, "link": link}
    send_mail(
        subject=f"You've been invited to {membership.org.name}",
        message=render_to_string("orgs/emails/invite.txt", context),
        from_email=None,
        recipient_list=[user.email],
    )
