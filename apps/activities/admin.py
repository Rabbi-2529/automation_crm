from django.contrib import admin

from .models import Activity, Attachment, Note


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ["summary", "kind", "contact", "company", "actor", "org", "created_at"]
    list_filter = ["org", "kind"]
    raw_id_fields = ["contact", "company", "actor", "note", "attachment"]


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ["__str__", "contact", "company", "author", "org", "created_at"]
    raw_id_fields = ["contact", "company", "author"]


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ["original_name", "size", "contact", "company", "uploaded_by", "org", "created_at"]
    raw_id_fields = ["contact", "company", "uploaded_by"]
