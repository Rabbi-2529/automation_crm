from celery import shared_task

from .emails import send_due_reports


@shared_task
def send_scheduled_reports():
    return send_due_reports()
