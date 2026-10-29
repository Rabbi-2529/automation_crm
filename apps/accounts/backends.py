from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """ModelBackend with case-insensitive email lookup."""

    def authenticate(self, request, username=None, password=None, email=None, **kwargs):
        email = (email or username or "").strip().lower()
        return super().authenticate(request, username=email, password=password, **kwargs)
