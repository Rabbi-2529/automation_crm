from django.urls import path

from . import tables, views

app_name = "contacts"

urlpatterns = [
    path("contacts/", views.ContactListView.as_view(), name="contact_list"),
    path("contacts/new/", views.ContactCreateView.as_view(), name="contact_create"),
    path("contacts/lookup/", views.ContactLookupView.as_view(), name="contact_lookup"),
    path("contacts/table/", tables.ContactTableView.as_view(), name="contact_table"),
    path("contacts/bulk/", views.ContactBulkActionView.as_view(), name="contact_bulk"),
    path("contacts/export/", views.ContactExportView.as_view(), name="contact_export"),
    path("contacts/<int:pk>/", views.ContactDetailView.as_view(), name="contact_detail"),
    path("contacts/<int:pk>/edit/", views.ContactUpdateView.as_view(), name="contact_edit"),
    path("contacts/<int:pk>/delete/", views.ContactDeleteView.as_view(), name="contact_delete"),
    path("companies/", views.CompanyListView.as_view(), name="company_list"),
    path("companies/new/", views.CompanyCreateView.as_view(), name="company_create"),
    path("companies/lookup/", views.CompanyLookupView.as_view(), name="company_lookup"),
    path("companies/table/", tables.CompanyTableView.as_view(), name="company_table"),
    path("companies/export/", views.CompanyExportView.as_view(), name="company_export"),
    path("companies/<int:pk>/", views.CompanyDetailView.as_view(), name="company_detail"),
    path("companies/<int:pk>/edit/", views.CompanyUpdateView.as_view(), name="company_edit"),
    path("companies/<int:pk>/delete/", views.CompanyDeleteView.as_view(), name="company_delete"),
    path("imports/", views.ImportListView.as_view(), name="import_list"),
    path("imports/new/", views.ImportUploadView.as_view(), name="import_upload"),
    path("imports/<int:pk>/map/", views.ImportMappingView.as_view(), name="import_map"),
    path("imports/<int:pk>/", views.ImportDetailView.as_view(), name="import_detail"),
    path("settings/tags/", views.TagListView.as_view(), name="tag_list"),
    path("settings/tags/new/", views.TagCreateView.as_view(), name="tag_create"),
    path("settings/tags/<int:pk>/", views.TagUpdateView.as_view(), name="tag_edit"),
    path("settings/tags/<int:pk>/delete/", views.TagDeleteView.as_view(), name="tag_delete"),
    path("settings/fields/", views.CustomFieldListView.as_view(), name="custom_field_list"),
    path("settings/fields/new/", views.CustomFieldCreateView.as_view(), name="custom_field_create"),
    path("settings/fields/<int:pk>/", views.CustomFieldUpdateView.as_view(), name="custom_field_edit"),
    path("settings/fields/<int:pk>/delete/", views.CustomFieldDeleteView.as_view(), name="custom_field_delete"),
]
