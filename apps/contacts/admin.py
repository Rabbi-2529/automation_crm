from django.contrib import admin

from .models import Company, Contact, CustomFieldDefinition, ImportJob, Tag


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ["__str__", "email", "phone", "company", "lifecycle_stage", "owner", "org", "created_at"]
    list_filter = ["org", "lifecycle_stage", "source"]
    search_fields = ["first_name", "last_name", "email", "phone"]
    raw_id_fields = ["company", "owner", "created_by"]
    filter_horizontal = ["tags"]


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "domain", "industry", "owner", "org", "created_at"]
    list_filter = ["org"]
    search_fields = ["name", "domain"]
    raw_id_fields = ["owner", "created_by"]
    filter_horizontal = ["tags"]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "color", "org"]
    list_filter = ["org"]


@admin.register(CustomFieldDefinition)
class CustomFieldDefinitionAdmin(admin.ModelAdmin):
    list_display = ["label", "key", "entity", "field_type", "required", "org"]
    list_filter = ["org", "entity", "field_type"]


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    list_display = ["original_name", "status", "total_rows", "created_count", "updated_count", "error_count", "org", "created_at"]
    list_filter = ["org", "status"]
    readonly_fields = ["errors", "mapping", "headers"]
