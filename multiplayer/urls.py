from django.urls import path
from . import views

app_name = "multiplayer"

urlpatterns = [
    path('rooms/create', views.RoomCreateView.as_view(), name='room_create'),
    path('rooms/<str:code>/', views.RoomDetailView.as_view(), name='room_detail'),
    path('rooms/', views.RoomListView.as_view(), name='room_list'),
    path('rooms/<str:code>/join', views.room_join, name='room_join'),
    path('rooms/<str:code>/quit', views.RoomPlayerDeleteView.as_view(), name='room_quit'),
]