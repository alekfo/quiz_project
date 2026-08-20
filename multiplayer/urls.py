from django.urls import path
from . import views

app_name = "multiplayer"

urlpatterns = [
    path('room/create', views.RoomCreateView.as_view(), name='room_create'),
    path('room/<str:code>/', views.RoomDetailView.as_view(), name='room_detail')
]