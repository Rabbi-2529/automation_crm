from .services import unread_count


def inbox_counts(request):
    """Badge on the sidebar's Inbox link."""
    if not getattr(request, "membership", None):
        return {}
    return {"inbox_unread_count": unread_count(request.org)}
