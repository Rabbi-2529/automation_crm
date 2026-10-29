"""Creates a ready-to-try demo workspace: admin/manager/agent logins and sample contacts, deals and tasks.

Logins come from DEMO_EMAIL / DEMO_PASSWORD (the .env file); the manager and agent use the same password with
manager@ / agent@ on the same domain. Safe to run again: existing records are left as they are.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

import environ
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.activities.models import ActivityKind, Note
from apps.activities.services import record
from apps.contacts.models import Company, Contact, Source, Tag
from apps.orgs.models import Membership, Organization, Role, Team
from apps.pipeline import services as pipeline_services
from apps.pipeline.models import Deal, DealStatus, LostReason
from apps.reports.models import SalesTarget
from apps.tasks.models import Priority, Task, TaskKind

WORKSPACE = "Demo Traders"
COMPANIES = [("Pathao Logistics", "pathao.example.com", "Logistics"), ("Shwapno Retail", "shwapno.example.com", "Retail"),
             ("Daraz Sellers Hub", "daraz.example.com", "E-commerce"), ("Chaldal Grocery", "chaldal.example.com", "E-commerce"),
             ("Bikroy Classifieds", "bikroy.example.com", "Marketplace")]
CONTACTS = [
    ("Karim", "Hasan", "karim@pathao.example.com", "01711000002", "Operations Head", 0, "customer", ["VIP"]),
    ("Nadia", "Islam", "nadia@shwapno.example.com", "01811000003", "Marketing Manager", 1, "opportunity", ["Website"]),
    ("Tanvir", "Ahmed", "tanvir@daraz.example.com", "01911000004", "Founder", 2, "qualified", ["Facebook"]),
    ("Farhan", "Rahman", "farhan@chaldal.example.com", "01611000005", "CTO", 3, "opportunity", []),
    ("Sadia", "Chowdhury", "sadia@bikroy.example.com", "01511000006", "Sales Lead", 4, "lead", ["Website"]),
    ("Rafi", "Uddin", "rafi@example.com", "01712000007", "Shop owner", None, "lead", ["Facebook"]),
    ("Mitu", "Akter", "mitu@example.com", "01812000008", "Freelancer", None, "lead", []),
    ("Imran", "Hossain", "imran@pathao.example.com", "01912000009", "Fleet Manager", 0, "customer", ["VIP"]),
]
DEALS = [  # title, value, status, contact index, days ago created, close in days (open) / closed days ago
    ("Pathao fleet CRM", 320000, DealStatus.WON, 0, 40, 12), ("Shwapno loyalty", 180000, DealStatus.WON, 1, 30, 6),
    ("Daraz sellers pilot", 95000, DealStatus.LOST, 2, 25, 4), ("Chaldal renewal", 210000, DealStatus.OPEN, 3, 10, 14),
    ("Bikroy upgrade", 75000, DealStatus.OPEN, 4, 8, 21), ("Imran fleet add-on", 120000, DealStatus.OPEN, 7, 3, 7),
    ("Rafi shop starter", 25000, DealStatus.OPEN, 5, 1, 30),
]
TASKS = [  # title, kind, priority, due in hours, contact index
    ("Call Nadia about the loyalty roll-out", TaskKind.CALL, Priority.HIGH, -20, 1),
    ("Send pricing to Tanvir", TaskKind.EMAIL, Priority.NORMAL, 3, 2),
    ("Demo meeting with Chaldal", TaskKind.MEETING, Priority.HIGH, 26, 3),
    ("Follow up Sadia's web enquiry", TaskKind.FOLLOW_UP, Priority.NORMAL, 50, 4),
]


class Command(BaseCommand):
    help = "Create the demo workspace and logins (from DEMO_EMAIL / DEMO_PASSWORD) with sample data."

    def add_arguments(self, parser):
        parser.add_argument("--reset-password", action="store_true", help="Set the demo logins' password to DEMO_PASSWORD again.")
        parser.add_argument("--allow-production", action="store_true", help="Run even when DEBUG is off.")

    def handle(self, *args, reset_password=False, allow_production=False, **options):
        if not settings.DEBUG and not allow_production:
            raise CommandError("DEBUG is off. Demo data doesn't belong in production; pass --allow-production if you're sure.")
        env = environ.Env()
        email, password = env.str("DEMO_EMAIL", default="").strip().lower(), env.str("DEMO_PASSWORD", default="")
        if not email or "@" not in email or len(password) < 8:
            raise CommandError("Set DEMO_EMAIL and DEMO_PASSWORD (8+ characters) in .env first.")
        domain = email.split("@", 1)[1]

        with transaction.atomic():
            org = Organization.objects.filter(name=WORKSPACE).first() or Organization.objects.create(name=WORKSPACE, currency="BDT",
                                                                                                timezone="Asia/Dhaka")
            people = {}
            for role, address, first, last in ((Role.ADMIN, email, "Demo", "Admin"),
                                               (Role.MANAGER, f"manager@{domain}", "Mina", "Manager"),
                                               (Role.AGENT, f"agent@{domain}", "Arif", "Agent")):
                user = User.objects.filter(email=address).first()
                if user is None:
                    user = User.objects.create_user(email=address, password=password, first_name=first, last_name=last)
                elif reset_password:
                    user.set_password(password)
                    user.save(update_fields=["password"])
                Membership.objects.get_or_create(org=org, user=user, defaults={"role": role})
                people[role] = user
            created = self.sample_data(org, people)

        self.stdout.write(self.style.SUCCESS(f"Demo workspace “{WORKSPACE}” is ready ({created} new sample records)."))
        self.stdout.write(f"Log in as admin with DEMO_EMAIL, or as manager@{domain} / agent@{domain}; all use DEMO_PASSWORD from .env.")

    def sample_data(self, org, people):
        created = 0
        today = timezone.localdate()
        agent, manager = people[Role.AGENT], people[Role.MANAGER]
        team, _ = Team.objects.get_or_create(org=org, name="Dhaka sales", defaults={"description": "Field and phone sales"})
        team.members.add(*Membership.objects.filter(org=org, user__in=[agent, manager]))

        tags = {name: Tag.objects.get_or_create(org=org, name=name, defaults={"color": color})[0]
                for name, color in (("VIP", "violet"), ("Website", "sky"), ("Facebook", "emerald"))}
        companies = []
        for name, domain, industry in COMPANIES:
            company, new = Company.objects.get_or_create(org=org, name=name, defaults={"domain": domain, "industry": industry,
                                                                                    "city": "Dhaka", "country": "Bangladesh"})
            companies.append(company)
            created += new

        contacts = []
        for i, (first, last, address, phone, title, company, stage, tag_names) in enumerate(CONTACTS):
            contact = Contact.objects.for_org(org).filter(email=address).first()
            if contact is None:
                contact = Contact.objects.create(
                    org=org, first_name=first, last_name=last, email=address, phone=phone, job_title=title, lifecycle_stage=stage,
                    company=companies[company] if company is not None else None, owner=agent if i % 2 else manager,
                    source=[Source.MANUAL, Source.WEB_FORM, Source.FACEBOOK][i % 3])
                contact.tags.add(*(tags[t] for t in tag_names))
                record(org=org, kind=ActivityKind.CREATED, actor=contact.owner, contact=contact, summary="Contact created (demo data)")
                created += 1
            contacts.append(contact)
        if not Note.objects.filter(org=org, contact=contacts[0]).exists():
            note = Note.objects.create(org=org, contact=contacts[0], author=manager, body="Happy with the fleet CRM. Ask about the add-on in Q1.")
            record(org=org, kind=ActivityKind.NOTE_ADDED, actor=manager, contact=contacts[0], note=note, summary="Added a note")

        pipeline = pipeline_services.default_pipeline(org)
        stages = list(pipeline.stages.order_by("position"))
        reason = LostReason.objects.for_org(org).first()
        for i, (title, value, status, contact_i, age, days) in enumerate(DEALS):
            if Deal.objects.for_org(org).filter(title=title).exists():
                continue
            contact = contacts[contact_i]
            deal = Deal.objects.create(
                org=org, pipeline=pipeline, stage=stages[min(i % len(stages), len(stages) - 1)], title=title, value=Decimal(value),
                contact=contact, company=contact.company, owner=contact.owner, currency=org.currency, created_by=contact.owner,
                expected_close_date=today + timedelta(days=days) if status == DealStatus.OPEN else None)
            pipeline_services.deal_created(deal, contact.owner)
            if status == DealStatus.WON:
                pipeline_services.mark_won(deal, contact.owner)
            elif status == DealStatus.LOST:
                pipeline_services.mark_lost(deal, contact.owner, reason, "Went with a cheaper tool")
            stamp = timezone.make_aware(datetime.combine(today - timedelta(days=age), time(11)))
            closed = timezone.make_aware(datetime.combine(today - timedelta(days=days), time(15))) if status != DealStatus.OPEN else None
            Deal.objects.filter(pk=deal.pk).update(created_at=stamp, closed_at=closed)
            created += 1

        now = timezone.now()
        for title, kind, priority, hours, contact_i in TASKS:
            if Task.objects.for_org(org).filter(title=title).exists():
                continue
            contact = contacts[contact_i]
            Task.objects.create(org=org, title=title, kind=kind, priority=priority, due_at=now + timedelta(hours=hours),
                                assignee=contact.owner, created_by=manager, contact=contact)
            created += 1

        SalesTarget.objects.get_or_create(org=org, user=None, metric="revenue", period="month", period_start=today.replace(day=1),
                                          defaults={"value": Decimal(1500000)})
        return created
