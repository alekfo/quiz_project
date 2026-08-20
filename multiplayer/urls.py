from django.urls import path
from . import views

app_name = "multiplayer"

urlpatterns = [
    path('room/create', views.room_create, name='room_create'),
]