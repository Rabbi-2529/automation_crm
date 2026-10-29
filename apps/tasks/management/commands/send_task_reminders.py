from django.core.management.base import BaseCommand

from apps.tasks.services import send_due_reminders


class Command(BaseCommand):
    help = "Email task reminders that are due. Celery beat runs this every minute; use cron if you don't run beat."

    def handle(self, *args, **options):
        self.stdout.write(f"Sent {send_due_reminders()} reminder(s).")
