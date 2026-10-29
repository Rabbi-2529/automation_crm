from django.db.models import Count, Q
from django.utils import timezone

from .models import Task, today_bounds


def task_counts(request):
    """Badge on the sidebar's Tasks link: my open tasks that are overdue or due today."""
    if not getattr(request, "membership", None):
        return {}
    _, end_of_today = today_bounds()
    counts = Task.objects.open().filter(org=request.org, assignee=request.user).aggregate(
        due=Count("pk", filter=Q(due_at__lt=end_of_today)),
        overdue=Count("pk", filter=Q(due_at__lt=timezone.now())),
    )
    return {"my_task_due_count": counts["due"], "my_task_overdue_count": counts["overdue"]}
