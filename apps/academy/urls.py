from django.urls import path

from apps.academy import views

app_name = "academy"

urlpatterns = [
    path("students/", views.student_list, name="student_list"),
    path("students/new/", views.student_create, name="student_create"),
    path("students/<uuid:pk>/", views.student_detail, name="student_detail"),
    path("students/<uuid:pk>/edit/", views.student_edit, name="student_edit"),
    path("students/<uuid:pk>/status/", views.student_change_status, name="student_change_status"),
    path("batches/", views.batch_list, name="batch_list"),
    path("batches/new/", views.batch_create, name="batch_create"),
    path("batches/<uuid:pk>/", views.batch_detail, name="batch_detail"),
]
