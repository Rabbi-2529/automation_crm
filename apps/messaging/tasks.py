from celery import shared_task

from .inbound import fetch_inbox
from .models import ChannelSettings, Message
from .services import deliver


@shared_task(acks_late=True)
def deliver_message(message_id):
    message = Message.objects.select_related("org", "contact", "deal").filter(pk=message_id).first()
    if message:
        deliver(message)


@shared_task
def fetch_inbox_for(settings_id):
    channel_settings = ChannelSettings.objects.select_related("org").get(pk=settings_id)
    return fetch_inbox(channel_settings)


@shared_task
def fetch_all_inboxes():
    ids = ChannelSettings.objects.filter(imap_enabled=True, org__is_active=True).exclude(imap_host="").values_list("pk", flat=True)
    for settings_id in ids:
        fetch_inbox_for.delay(settings_id)
    return len(ids)
