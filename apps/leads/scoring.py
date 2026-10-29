"""Lead scoring: score = profile points (rules whose conditions currently match) + behaviour points (logged events)."""

from django.db import transaction
from django.db.models import Q, Sum

from apps.activities.models import ActivityKind
from apps.activities.services import record

from .models import LeadSettings, ScoreLog, ScoringEvent, ScoringRule

KIND_TO_EVENT = {
    ActivityKind.FORM_SUBMITTED: ScoringEvent.FORM_SUBMITTED,
    ActivityKind.EMAIL_OPENED: ScoringEvent.EMAIL_OPENED,
    ActivityKind.EMAIL_CLICKED: ScoringEvent.EMAIL_CLICKED,
    ActivityKind.EMAIL_RECEIVED: ScoringEvent.EMAIL_RECEIVED,
    ActivityKind.SMS_RECEIVED: ScoringEvent.SMS_RECEIVED,
    ActivityKind.CALL_LOGGED: ScoringEvent.CALL_LOGGED,
    ActivityKind.UNSUBSCRIBED: ScoringEvent.UNSUBSCRIBED,
}
GRADE_LABELS = {"cold": "Cold", "warm": "Warm", "hot": "Hot"}


def event_for(activity):
    if activity.kind == ActivityKind.CREATED and activity.deal_id:
        return ScoringEvent.DEAL_CREATED
    return KIND_TO_EVENT.get(activity.kind)


def dispatch(activity):
    """After-commit hook from activities.record(): queue scoring only when the event can matter."""
    from .tasks import score_activity

    if activity.kind in (ActivityKind.LEAD_GRADE_CHANGED, ActivityKind.AUTOMATION):
        return
    if not (activity.contact_id or activity.deal_id):
        return
    if ScoringRule.objects.filter(org_id=activity.org_id, is_active=True).exists():
        score_activity.delay(activity.pk)


@transaction.atomic
def apply_activity(activity):
    contact = activity.contact or (activity.deal.contact if activity.deal_id else None)
    if contact is None:
        return None
    event = event_for(activity)
    if event:
        for rule in ScoringRule.objects.filter(org_id=activity.org_id, is_active=True, kind=ScoringRule.EVENT, event=event):
            if rule.max_times and ScoreLog.objects.filter(contact=contact, rule=rule).count() >= rule.max_times:
                continue
            ScoreLog.objects.create(contact=contact, rule=rule, points=rule.points, reason=f"{rule.name}: {activity.summary}"[:200])
    return recompute(contact)


def profile_points(contact, rules):
    from apps.automation.conditions import matches

    return sum(rule.points for rule in rules if matches(rule.conditions, contact=contact))


def recompute(contact, rules=None, settings=None):
    """Recalculates score and grade; records a timeline event when the grade changes. Returns the new score."""
    if rules is None:
        rules = list(ScoringRule.objects.filter(org_id=contact.org_id, is_active=True, kind=ScoringRule.PROFILE))
    settings = settings or LeadSettings.for_org(contact.org)
    # Manual / workflow adjustments have no rule and always count.
    behaviour = ScoreLog.objects.filter(Q(rule__isnull=True) | Q(rule__is_active=True), contact=contact).aggregate(p=Sum("points"))["p"] or 0
    score = profile_points(contact, rules) + behaviour
    grade = settings.grade_for(score)
    if (score, grade) == (contact.score, contact.grade):
        return score
    old_grade = contact.grade
    type(contact).objects.filter(pk=contact.pk).update(score=score, grade=grade)
    contact.score, contact.grade = score, grade
    if grade != old_grade:
        record(org=contact.org, kind=ActivityKind.LEAD_GRADE_CHANGED, contact=contact,
               summary=f"Lead grade: {GRADE_LABELS[old_grade]} → {GRADE_LABELS[grade]} (score {score})",
               data={"from": old_grade, "to": grade, "score": score})
    return score


def recompute_org(org):
    from apps.contacts.models import Contact

    rules = list(ScoringRule.objects.filter(org=org, is_active=True, kind=ScoringRule.PROFILE))
    settings = LeadSettings.for_org(org)
    count = 0
    for contact in Contact.objects.filter(org=org).select_related("company", "org").iterator(chunk_size=500):
        recompute(contact, rules, settings)
        count += 1
    return count
