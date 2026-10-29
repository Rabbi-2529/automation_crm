from django.urls import path

from . import views

app_name = "activities"

urlpatterns = [
    path("<str:target>/<int:pk>/notes/", views.NoteCreateView.as_view(), name="note_create"),
    path("notes/<int:pk>/delete/", views.NoteDeleteView.as_view(), name="note_delete"),
    path("<str:target>/<int:pk>/files/", views.AttachmentUploadView.as_view(), name="attachment_upload"),
    path("files/<int:pk>/", views.AttachmentDownloadView.as_view(), name="attachment_download"),
    path("files/<int:pk>/delete/", views.AttachmentDeleteView.as_view(), name="attachment_delete"),
]
