import socket

from celery import shared_task
from django.utils import timezone


@shared_task
def ping():
    """Round-trip check used by the System status page."""
    return {"pong": True, "at": timezone.now().isoformat(), "host": socket.gethostname()}
