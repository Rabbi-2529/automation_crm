from django.urls import path

from . import views

app_name = "campaigns"

urlpatterns = [
    path("segments/", views.SegmentListView.as_view(), name="segment_list"),
    path("segments/new/", views.SegmentEditView.as_view(), name="segment_create"),
    path("segments/<int:pk>/", views.SegmentEditView.as_view(), name="segment_edit"),
    path("segments/<int:pk>/delete/", views.SegmentDeleteView.as_view(), name="segment_delete"),
    path("sequences/", views.SequenceListView.as_view(), name="sequence_list"),
    path("sequences/new/", views.SequenceEditView.as_view(), name="sequence_create"),
    path("sequences/<int:pk>/", views.SequenceDetailView.as_view(), name="sequence_detail"),
    path("sequences/<int:pk>/edit/", views.SequenceEditView.as_view(), name="sequence_edit"),
    path("sequences/<int:pk>/delete/", views.SequenceDeleteView.as_view(), name="sequence_delete"),
    path("sequences/<int:pk>/enroll-segment/", views.SequenceEnrollSegmentView.as_view(), name="sequence_enroll_segment"),
    path("sequences/enrollments/<int:pk>/remove/", views.EnrollmentRemoveView.as_view(), name="enrollment_remove"),
    path("contacts/<int:contact_pk>/sequences/", views.ContactEnrollView.as_view(), name="contact_enroll"),
    path("broadcasts/", views.BroadcastListView.as_view(), name="broadcast_list"),
    path("broadcasts/new/", views.BroadcastEditView.as_view(), name="broadcast_create"),
    path("broadcasts/<int:pk>/", views.BroadcastDetailView.as_view(), name="broadcast_detail"),
    path("broadcasts/<int:pk>/edit/", views.BroadcastEditView.as_view(), name="broadcast_edit"),
    path("broadcasts/<int:pk>/delete/", views.BroadcastDeleteView.as_view(), name="broadcast_delete"),
]
