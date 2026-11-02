from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/multiplayer/rooms/(?P<code>\w+)/$', consumers.RoomConsumer.as_asgi()),
]
