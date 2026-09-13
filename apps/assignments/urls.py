from django.urls import path

from apps.assignments import views

app_name = "assignments"

urlpatterns = [
    path("assignments/", views.assignment_list, name="assignment_list"),
    path("api/webhooks/lms/", views.lms_webhook, name="lms_webhook"),
]
