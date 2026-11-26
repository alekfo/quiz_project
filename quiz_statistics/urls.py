from django.urls import path

from . import views


app_name = "quiz_statistics"

urlpatterns = [
    path("rankings/", views.rankings, name="rankings"),
    path("notifications/", views.notification_list, name="notifications"),
    path("notifications/<int:pk>/read/", views.notification_read, name="notification_read"),
]
