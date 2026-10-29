from celery import shared_task

from .services import send_due_reminders


@shared_task
def send_task_reminders():
    return send_due_reminders()
