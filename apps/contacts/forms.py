from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.text import slugify

from .custom_fields import CustomFieldFormMixin, definitions_for
from .models import (
    Company,
    Contact,
    CustomFieldDefinition,
    DuplicateStrategy,
    Entity,
    FieldType,
    ImportJob,
    LifecycleStage,
    Tag,
    phone_digits,
)

User = get_user_model()


def org_users(org):
    return User.objects.filter(memberships__org=org, memberships__is_active=True).order_by("first_name", "email")


class OrgModelForm(forms.ModelForm):
    """ModelForm bound to the current org: limits FK/M2M choices and stamps `org` on save."""

    def __init__(self, *args, org, **kwargs):
        self.org = org
        super().__init__(*args, **kwargs)
        if "owner" in self.fields:
            self.fields["owner"].queryset = org_users(org)
            self.fields["owner"].label_from_instance = lambda u: u.display_name
        if "tags" in self.fields:
            self.fields["tags"].queryset = Tag.objects.for_org(org)
            self.fields["tags"].widget.attrs["data-placeholder"] = "Add tags…"
        if "company" in self.fields:
            self.fields["company"].queryset = Company.objects.for_org(org)

    def save(self, commit=True):
        self.instance.org = self.org
        return super().save(commit)


class ContactForm(CustomFieldFormMixin, OrgModelForm):
    new_company = forms.CharField(
        max_length=200, required=False, label="…or create a new company", help_text="Leave empty to use the company above."
    )
    confirm_duplicate = forms.BooleanField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Contact
        fields = ["first_name", "last_name", "email", "phone", "job_title", "company", "owner", "lifecycle_stage", "tags"]

    field_order = [
        "first_name", "last_name", "email", "phone", "job_title", "company", "new_company", "owner", "lifecycle_stage", "tags",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_custom_fields(definitions_for(self.org, Entity.CONTACT))
        self.possible_duplicates = []

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if email:
            clash = Contact.objects.for_org(self.org).filter(email=email).exclude(pk=self.instance.pk).first()
            if clash:
                raise forms.ValidationError(f"{clash.display_name} already uses this email.")
        return email

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(f) for f in ("first_name", "last_name", "email", "phone")):
            raise forms.ValidationError("Give the contact at least a name, an email or a phone number.")
        if not self.errors and not cleaned.get("confirm_duplicate"):
            self.possible_duplicates = self.find_possible_duplicates(cleaned)
            if self.possible_duplicates:
                # Shown with a "save anyway" button that re-posts with confirm_duplicate=1.
                self.data = self.data.copy()
                self.data["confirm_duplicate"] = "1"
                raise forms.ValidationError("This looks like a contact you already have. Check below, or save anyway.")
        return cleaned

    def find_possible_duplicates(self, cleaned):
        match = Q()
        digits = phone_digits(cleaned.get("phone"))
        if len(digits) >= 7:
            # Compare the last 10 digits so +8801711… and 01711… match.
            match |= Q(phone_digits__endswith=digits[-10:])
        first, last = cleaned.get("first_name", "").strip(), cleaned.get("last_name", "").strip()
        if first and last:
            match |= Q(first_name__iexact=first, last_name__iexact=last)
        if not match:
            return []
        return list(Contact.objects.for_org(self.org).filter(match).exclude(pk=self.instance.pk).select_related("company")[:5])

    def save(self, commit=True):
        new_company = self.cleaned_data.get("new_company", "").strip()
        if new_company:
            self.instance.company = Company.objects.for_org(self.org).filter(name__iexact=new_company).first() or (
                Company.objects.create(org=self.org, name=new_company, created_by=self.instance.created_by)
            )
        self.instance.custom_fields = self.custom_field_values()
        return super().save(commit)


class CompanyForm(CustomFieldFormMixin, OrgModelForm):
    class Meta:
        model = Company
        fields = ["name", "domain", "industry", "phone", "website", "address", "city", "country", "owner", "tags"]
        help_texts = {"domain": "e.g. thingbd.com — used to match contacts by email domain later."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_custom_fields(definitions_for(self.org, Entity.COMPANY))

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = Company.objects.for_org(self.org).filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A company with this name already exists.")
        return name

    def save(self, commit=True):
        self.instance.custom_fields = self.custom_field_values()
        return super().save(commit)


class TagForm(OrgModelForm):
    class Meta:
        model = Tag
        fields = ["name", "color"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Tag.objects.for_org(self.org).filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A tag with this name already exists.")
        return name


class CustomFieldDefinitionForm(OrgModelForm):
    options_text = forms.CharField(
        label="Dropdown options", required=False, widget=forms.Textarea(attrs={"rows": 4}),
        help_text="One option per line. Only used for dropdown fields.",
    )

    class Meta:
        model = CustomFieldDefinition
        fields = ["entity", "label", "key", "field_type", "required", "position"]
        labels = {"key": "Key"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["key"].required = False
        self.fields["key"].help_text = "Leave empty to generate from the label. Used in CSV imports and automations."
        if self.instance.pk:
            # Stored values are keyed by these, so they're fixed once created.
            for name in ("key", "entity", "field_type"):
                self.fields[name].disabled = True
            self.fields["key"].help_text = "Fixed once created."
        self.initial.setdefault("options_text", "\n".join(self.instance.options or []))

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk:
            key = slugify(cleaned.get("key") or cleaned.get("label") or "").replace("-", "_")
            if not key:
                self.add_error("key", "Enter a key or a label.")
            elif CustomFieldDefinition.objects.for_org(self.org).filter(entity=cleaned.get("entity"), key=key).exists():
                self.add_error("key", "This key is already used.")
            cleaned["key"] = key
        options = [line.strip() for line in cleaned.get("options_text", "").splitlines() if line.strip()]
        field_type = cleaned.get("field_type") or self.instance.field_type
        if field_type == FieldType.SELECT and not options:
            self.add_error("options_text", "Add at least one option.")
        cleaned["options"] = options
        return cleaned

    def save(self, commit=True):
        if not self.instance.pk:
            self.instance.key = self.cleaned_data["key"]
        self.instance.options = self.cleaned_data["options"]
        return super().save(commit)


class ImportUploadForm(forms.Form):
    file = forms.FileField(label="CSV file", help_text="First row must be column headers. UTF-8, comma separated.")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a .csv file.")
        if upload.size > settings.IMPORT_MAX_MB * 1024 * 1024:
            raise forms.ValidationError(f"File is larger than {settings.IMPORT_MAX_MB} MB.")
        return upload


class ImportMappingForm(forms.ModelForm):
    """One dropdown per CSV header, plus import options."""

    class Meta:
        model = ImportJob
        fields = ["duplicate_strategy", "default_owner", "add_tags"]
        labels = {"duplicate_strategy": "When the email already exists", "add_tags": "Tag every imported contact with"}
        widgets = {"duplicate_strategy": forms.RadioSelect}

    def __init__(self, *args, targets, **kwargs):
        super().__init__(*args, **kwargs)
        org = self.instance.org
        self.fields["default_owner"].queryset = org_users(org)
        self.fields["default_owner"].label_from_instance = lambda u: u.display_name
        self.fields["default_owner"].help_text = "Used when the CSV has no owner column or the cell is empty."
        self.fields["add_tags"].queryset = Tag.objects.for_org(org)
        self.fields["add_tags"].widget.attrs["data-placeholder"] = "No extra tags"
        self.fields["duplicate_strategy"].choices = DuplicateStrategy.choices
        self.column_names = []
        guesses = guess_mapping(self.instance.headers, targets)
        for index, header in enumerate(self.instance.headers):
            name = f"col_{index}"
            self.column_names.append(name)
            self.fields[name] = forms.ChoiceField(
                label=header or f"Column {index + 1}", required=False,
                choices=[("", "— Don't import —")] + targets,
                initial=self.instance.mapping.get(header, guesses.get(header, "")),
            )

    def column_fields(self):
        return [self[name] for name in self.column_names]

    def option_fields(self):
        return [self[name] for name in ("duplicate_strategy", "default_owner", "add_tags")]

    def clean(self):
        cleaned = super().clean()
        chosen = [cleaned.get(name) for name in self.column_names if cleaned.get(name)]
        duplicates = {target for target in chosen if chosen.count(target) > 1 and target != "tags"}
        if duplicates:
            raise forms.ValidationError("Each field can be mapped from one column only (tags can come from several).")
        if not set(chosen) & {"first_name", "last_name", "email", "phone"}:
            raise forms.ValidationError("Map at least one of: first name, last name, email or phone.")
        return cleaned

    def save(self, commit=True):
        self.instance.mapping = {
            header: self.cleaned_data[f"col_{index}"]
            for index, header in enumerate(self.instance.headers)
            if self.cleaned_data.get(f"col_{index}")
        }
        return super().save(commit)


def guess_mapping(headers, targets):
    """Pre-selects targets whose key or label matches a header ("E-mail", "First Name", "cf: Budget" …)."""
    normalise = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())  # noqa: E731
    aliases = {"mail": "email", "emailaddress": "email", "mobile": "phone", "phonenumber": "phone", "name": "first_name",
               "firstname": "first_name", "lastname": "last_name", "surname": "last_name", "title": "job_title",
               "organization": "company", "organisation": "company", "companyname": "company", "stage": "lifecycle_stage"}
    by_name = {}
    for key, label in targets:
        names = [key, label]
        if key.startswith("cf:"):
            names += [key[3:], label.removesuffix(" (custom)")]
        for name in names:
            by_name.setdefault(normalise(name), key)
    guesses = {}
    for header in headers:
        n = normalise(header)
        guess = by_name.get(n) or aliases.get(n)
        if guess and guess not in guesses.values():
            guesses[header] = guess
    return guesses


class BulkActionForm(forms.Form):
    ACTIONS = [("add_tag", "Add tag"), ("remove_tag", "Remove tag"), ("assign_owner", "Assign owner"),
               ("set_stage", "Set lifecycle stage"), ("delete", "Delete")]

    action = forms.ChoiceField(choices=ACTIONS)
    ids = forms.ModelMultipleChoiceField(queryset=Contact.objects.none())
    tag = forms.ModelChoiceField(queryset=Tag.objects.none(), required=False)
    owner = forms.ModelChoiceField(queryset=User.objects.none(), required=False)
    stage = forms.ChoiceField(choices=[("", "—")] + LifecycleStage.choices, required=False)

    def __init__(self, *args, org, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ids"].queryset = Contact.objects.for_org(org)
        self.fields["tag"].queryset = Tag.objects.for_org(org)
        self.fields["owner"].queryset = org_users(org)

    def clean(self):
        cleaned = super().clean()
        needs = {"add_tag": "tag", "remove_tag": "tag", "assign_owner": "owner", "set_stage": "stage"}
        required = needs.get(cleaned.get("action"))
        if required and not cleaned.get(required):
            raise forms.ValidationError(f"Choose a {required} for this action.")
        return cleaned
