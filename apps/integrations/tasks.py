from celery import shared_task

from . import facebook, webhooks
from .models import FacebookLead, WebhookDelivery


@shared_task(acks_late=True)
def deliver_webhook(delivery_id):
    delivery = WebhookDelivery.objects.select_related("endpoint", "endpoint__org").filter(pk=delivery_id).first()
    if delivery:
        webhooks.deliver(delivery)


@shared_task
def retry_webhook_deliveries():
    return webhooks.retry_due()


@shared_task
def prune_webhook_deliveries():
    return webhooks.prune()


@shared_task(acks_late=True)
def import_facebook_lead(lead_id):
    lead = FacebookLead.objects.select_related("page", "org").filter(pk=lead_id).first()
    if lead:
        facebook.process(lead)


@shared_task
def retry_facebook_leads():
    return facebook.retry_failed()
