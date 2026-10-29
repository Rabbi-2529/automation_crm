from django.urls import path

from . import calendar, views

app_name = "tasks"

urlpatterns = [
    path("", views.TaskListView.as_view(), name="task_list"),
    path("calendar/", calendar.CalendarView.as_view(), name="calendar"),
    path("calendar/events/", calendar.CalendarEventsView.as_view(), name="calendar_events"),
    path("calendar/move/", calendar.CalendarMoveView.as_view(), name="calendar_move"),
    path("new/", views.TaskCreateView.as_view(), name="task_create"),
    path("<int:pk>/", views.TaskUpdateView.as_view(), name="task_edit"),
    path("<int:pk>/delete/", views.TaskDeleteView.as_view(), name="task_delete"),
    path("<int:pk>/toggle/", views.TaskToggleView.as_view(), name="task_toggle"),
]
