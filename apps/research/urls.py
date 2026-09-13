from django.urls import path

from apps.research import views

app_name = "research"

urlpatterns = [
    path("research/", views.submission_list, name="submission_list"),
    path("research/criteria/", views.criteria_editor, name="criteria"),
    path("research/<uuid:pk>/", views.submission_detail, name="submission_detail"),
    path("research/<uuid:pk>/competitors/refresh/", views.refresh_competitors, name="refresh_competitors"),
]
