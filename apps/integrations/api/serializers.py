"""REST API shapes. Every serializer gets the workspace from context["org"] and only accepts ids from that workspace.

Outgoing webhook payloads reuse these, so a contact looks the same in an API response and in a webhook.
"""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.activities.models import Activity, Note
from apps.contacts import custom_fields
from apps.contacts.models import Company, Contact, Entity, Tag
from apps.orgs.models import Membership
from apps.pipeline.models import Deal, LostReason, Pipeline, Stage
from apps.tasks.models import Task

User = get_user_model()


class OrgPrimaryKeyField(serializers.PrimaryKeyRelatedField):
    """A foreign key limited to the request's workspace."""

    def get_queryset(self):
        return self.queryset.model.objects.for_org(self.context["org"])


class MemberField(serializers.PrimaryKeyRelatedField):
    """A user id that must be an active member of the workspace."""

    def __init__(self, **kwargs):
        kwargs.setdefault("allow_null", True)
        kwargs.setdefault("required", False)
        super().__init__(queryset=User.objects.all(), **kwargs)

    def get_queryset(self):
        org = self.context["org"]
        return User.objects.filter(memberships__org=org, memberships__is_active=True)


class TagNamesField(serializers.ListField):
    """Tags as a list of names. Unknown names are created on write."""

    child = serializers.CharField(max_length=50)

    def to_representation(self, manager):
        return [tag.name for tag in manager.all()]


class CustomFieldsField(serializers.DictField):
    def __init__(self, entity, **kwargs):
        self.entity = entity
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        definitions = {d.key: d for d in custom_fields.definitions_for(self.context["org"], self.entity)}
        clean, errors = {}, {}
        for key, value in data.items():
            definition = definitions.get(key)
            if definition is None:
                errors[key] = "Unknown custom field."
                continue
            if value is None or value == "":
                clean[key] = None
                continue
            try:
                clean[key] = custom_fields.coerce(definition, str(value))
            except ValueError as exc:
                errors[key] = str(exc)
        if errors:
            raise serializers.ValidationError(errors)
        return clean


class TaggedCustomFieldsMixin:
    """Saves `tags` (names) and merges `custom_fields` into what the record already has."""

    def _save_extras(self, instance, tags, custom):
        if custom is not None:
            values = dict(instance.custom_fields or {})
            for key, value in custom.items():
                if value is None:
                    values.pop(key, None)
                else:
                    values[key] = value
            instance.custom_fields = values
            instance.save(update_fields=["custom_fields", "updated_at"])
        if tags is not None:
            org = self.context["org"]
            objs = []
            for name in {n.strip() for n in tags if n.strip()}:
                tag = Tag.objects.for_org(org).filter(name__iexact=name).first() or Tag.objects.create(org=org, name=name)
                objs.append(tag)
            instance.tags.set(objs)

    def create(self, validated_data):
        tags, custom = validated_data.pop("tags", None), validated_data.pop("custom_fields", None)
        instance = super().create(validated_data)
        self._save_extras(instance, tags, custom)
        return instance

    def update(self, instance, validated_data):
        tags, custom = validated_data.pop("tags", None), validated_data.pop("custom_fields", None)
        instance = super().update(instance, validated_data)
        self._save_extras(instance, tags, custom)
        return instance


class CompanySerializer(TaggedCustomFieldsMixin, serializers.ModelSerializer):
    owner = MemberField()
    tags = TagNamesField(required=False)
    custom_fields = CustomFieldsField(Entity.COMPANY)

    class Meta:
        model = Company
        fields = ["id", "name", "domain", "industry", "phone", "website", "address", "city", "country", "owner", "tags",
                  "custom_fields", "last_activity_at", "created_at", "updated_at"]
        read_only_fields = ["last_activity_at", "created_at", "updated_at"]


class ContactSerializer(TaggedCustomFieldsMixin, serializers.ModelSerializer):
    company = OrgPrimaryKeyField(queryset=Company.objects.all(), required=False, allow_null=True)
    company_name = serializers.CharField(source="company.name", read_only=True, default=None)
    owner = MemberField()
    tags = TagNamesField(required=False)
    custom_fields = CustomFieldsField(Entity.CONTACT)

    class Meta:
        model = Contact
        fields = ["id", "first_name", "last_name", "email", "phone", "job_title", "company", "company_name", "owner",
                  "lifecycle_stage", "source", "tags", "custom_fields", "score", "grade", "email_opt_out", "sms_opt_out",
                  "whatsapp_opt_out", "last_activity_at", "created_at", "updated_at"]
        read_only_fields = ["source", "score", "grade", "email_opt_out", "sms_opt_out", "whatsapp_opt_out", "last_activity_at",
                            "created_at", "updated_at"]

    def validate(self, attrs):
        email = attrs.get("email", getattr(self.instance, "email", ""))
        phone = attrs.get("phone", getattr(self.instance, "phone", ""))
        names = attrs.get("first_name", getattr(self.instance, "first_name", "")) or attrs.get("last_name", getattr(self.instance, "last_name", ""))
        if not (email or phone or names):
            raise serializers.ValidationError("Give the contact at least a name, email or phone.")
        return attrs


class StageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stage
        fields = ["id", "name", "position", "probability"]


class PipelineSerializer(serializers.ModelSerializer):
    stages = StageSerializer(many=True, read_only=True)

    class Meta:
        model = Pipeline
        fields = ["id", "name", "is_default", "stages"]


class DealSerializer(TaggedCustomFieldsMixin, serializers.ModelSerializer):
    pipeline = OrgPrimaryKeyField(queryset=Pipeline.objects.all(), required=False)
    stage = OrgPrimaryKeyField(queryset=Stage.objects.all(), required=False)
    stage_name = serializers.CharField(source="stage.name", read_only=True)
    contact = OrgPrimaryKeyField(queryset=Contact.objects.all(), required=False, allow_null=True)
    company = OrgPrimaryKeyField(queryset=Company.objects.all(), required=False, allow_null=True)
    owner = MemberField()
    currency = serializers.CharField(source="currency_code", read_only=True)
    lost_reason = serializers.CharField(source="lost_reason.name", read_only=True, default=None)
    weighted_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    tags = TagNamesField(required=False)
    custom_fields = CustomFieldsField(Entity.DEAL)

    class Meta:
        model = Deal
        fields = ["id", "title", "value", "currency", "pipeline", "stage", "stage_name", "status", "contact", "company", "owner",
                  "expected_close_date", "closed_at", "lost_reason", "lost_note", "weighted_value", "tags", "custom_fields",
                  "last_activity_at", "created_at", "updated_at"]
        read_only_fields = ["status", "closed_at", "lost_note", "last_activity_at", "created_at", "updated_at"]

    def validate(self, attrs):
        if self.instance is not None and "pipeline" in attrs and attrs["pipeline"] != self.instance.pipeline:
            raise serializers.ValidationError({"pipeline": "Deals can't change pipeline through the API."})
        pipeline = attrs.get("pipeline") or (self.instance.pipeline if self.instance else None)
        stage = attrs.get("stage")
        if pipeline and stage and stage.pipeline_id != pipeline.pk:
            raise serializers.ValidationError({"stage": "That stage belongs to another pipeline."})
        return attrs


class LostSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, help_text="Lost reason id or name.")
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)

    def validate_reason(self, value):
        if not value:
            return None
        reasons = LostReason.objects.for_org(self.context["org"])
        reason = (reasons.filter(pk=value).first() if value.isdigit() else None) or reasons.filter(name__iexact=value).first()
        if reason is None:
            raise serializers.ValidationError("Unknown lost reason.")
        return reason


class TaskSerializer(serializers.ModelSerializer):
    assignee = MemberField()
    contact = OrgPrimaryKeyField(queryset=Contact.objects.all(), required=False, allow_null=True)
    company = OrgPrimaryKeyField(queryset=Company.objects.all(), required=False, allow_null=True)
    deal = OrgPrimaryKeyField(queryset=Deal.objects.all(), required=False, allow_null=True)
    is_done = serializers.BooleanField(read_only=True)

    class Meta:
        model = Task
        fields = ["id", "title", "description", "kind", "priority", "due_at", "reminder", "assignee", "contact", "company", "deal",
                  "is_done", "completed_at", "created_at", "updated_at"]
        read_only_fields = ["completed_at", "created_at", "updated_at"]


class NoteSerializer(serializers.ModelSerializer):
    contact = OrgPrimaryKeyField(queryset=Contact.objects.all(), required=False, allow_null=True)
    company = OrgPrimaryKeyField(queryset=Company.objects.all(), required=False, allow_null=True)
    deal = OrgPrimaryKeyField(queryset=Deal.objects.all(), required=False, allow_null=True)
    author = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Note
        fields = ["id", "body", "contact", "company", "deal", "author", "created_at"]
        read_only_fields = ["created_at"]

    def validate(self, attrs):
        if sum(1 for k in ("contact", "company", "deal") if attrs.get(k)) != 1:
            raise serializers.ValidationError("Attach the note to exactly one of contact, company or deal.")
        return attrs


class ActivitySerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()

    class Meta:
        model = Activity
        fields = ["id", "kind", "summary", "data", "contact", "company", "deal", "actor", "created_at"]

    def get_actor(self, obj) -> dict | None:
        return {"id": obj.actor_id, "name": obj.actor.display_name} if obj.actor_id else None


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name", "color"]


class MemberSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="user.id")
    name = serializers.CharField(source="user.display_name")
    email = serializers.EmailField(source="user.email")

    class Meta:
        model = Membership
        fields = ["id", "name", "email", "role"]
