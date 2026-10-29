from django import forms


class LookupWidget(forms.Select):
    """Select2 AJAX picker for a ModelChoiceField with too many rows to list up front.

    Only the current value is rendered as an <option>; Select2 fetches the rest from `url?q=`, which must
    return {"results": [{"id", "text", "hint"}]}. The form sets `widget.queryset` to the field's org-scoped
    queryset so only allowed records can be shown as the current value.
    """

    def __init__(self, url, placeholder="Search…", attrs=None):
        super().__init__(attrs)
        self.url = url
        self.placeholder = placeholder
        self.queryset = None

    def get_context(self, name, value, attrs):
        self.choices = [("", "")]
        if value not in (None, "") and self.queryset is not None:
            obj = self.queryset.filter(pk=value).first()
            if obj is not None:
                self.choices.append((obj.pk, getattr(obj, "display_name", None) or str(obj)))
        attrs = {**(attrs or {}), "data-select2-ajax": str(self.url), "data-placeholder": self.placeholder}
        return super().get_context(name, value, attrs)
