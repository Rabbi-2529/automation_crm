from celery import shared_task

from apps.activities.models import Activity
from apps.orgs.models import Organization

from . import scoring


@shared_task(acks_late=True)
def score_activity(activity_id):
    activity = Activity.objects.select_related("org", "contact__org", "deal__contact__org").filter(pk=activity_id).first()
    return scoring.apply_activity(activity) if activity else None


@shared_task
def recompute_scores(org_id):
    return scoring.recompute_org(Organization.objects.get(pk=org_id))
