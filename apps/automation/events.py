"""Maps timeline Activities to workflow triggers and queues processing for workspaces that use them."""

from apps.activities.models import ActivityKind

# Activity kind -> trigger type, for activities on a contact (not about a deal).
CONTACT_KINDS = {
    ActivityKind.CREATED: "contact_created",
    ActivityKind.IMPORTED: "contact_created",
    ActivityKind.UPDATED: "contact_updated",
    ActivityKind.STAGE_CHANGED: "lifecycle_changed",
    ActivityKind.TAG_ADDED: "tag_added",
    ActivityKind.OWNER_CHANGED: "owner_changed",
    ActivityKind.EMAIL_OPENED: "email_opened",
    ActivityKind.EMAIL_CLICKED: "email_clicked",
    ActivityKind.EMAIL_RECEIVED: "email_received",
    ActivityKind.SMS_RECEIVED: "sms_received",
    ActivityKind.CALL_LOGGED: "call_logged",
    ActivityKind.TASK_COMPLETED: "task_completed",
}
DEAL_KINDS = {
    ActivityKind.CREATED: "deal_created",
    ActivityKind.DEAL_STAGE_CHANGED: "deal_stage_changed",
    ActivityKind.DEAL_WON: "deal_won",
    ActivityKind.DEAL_LOST: "deal_lost",
    ActivityKind.TASK_COMPLETED: "task_completed",
}


def trigger_type(activity):
    """The trigger this activity fires, or None. Deal events win over the contact they also touch."""
    if activity.deal_id:
        return DEAL_KINDS.get(activity.kind) or (CONTACT_KINDS.get(activity.kind) if activity.contact_id else None)
    if activity.contact_id:
        return CONTACT_KINDS.get(activity.kind)
    return None


def dispatch(activity):
    """Called after the activity's transaction commits. Cheap when the workspace has no matching workflow."""
    from .models import Workflow
    from .tasks import process_activity

    kind = trigger_type(activity)
    if kind and Workflow.objects.filter(org_id=activity.org_id, is_active=True, trigger_type=kind).exists():
        process_activity.delay(activity.pk)
