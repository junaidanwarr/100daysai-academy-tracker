from django.urls import path

from apps.assignments import views

app_name = "assignments"

urlpatterns = [
    path("assignments/", views.assignment_list, name="assignment_list"),
    path("assignments/new/", views.assignment_create, name="assignment_create"),
    path("assignments/<uuid:pk>/", views.assignment_detail, name="assignment_detail"),
    path("assignments/<uuid:pk>/edit/", views.assignment_edit, name="assignment_edit"),
    path("assignments/attempts/<uuid:pk>/", views.submission_detail, name="submission_detail"),
    path("api/webhooks/lms/", views.lms_webhook, name="lms_webhook"),
]
