from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from django.template.loader import render_to_string

from .models import Room
from .views import _get_room_context


class RoomConsumer(WebsocketConsumer):

    def connect(self):
        self.room_code = self.scope["url_route"]["kwargs"]["code"]
        user = self.scope["user"]

        if not user.is_authenticated:
            self.close()
            return

        room = Room.objects.prefetch_related("room_players").filter(token=self.room_code).first()
        if room is None:
            self.close()
            return

        is_host = room.host_id == user.id
        is_player = any(p.user_id == user.id for p in room.room_players.all())
        if not (is_host or is_player):
            self.close()
            return

        self.group_name = f"room_{self.room_code}"
        async_to_sync(self.channel_layer.group_add)(self.group_name, self.channel_name)
        self.accept()

    def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            async_to_sync(self.channel_layer.group_discard)(self.group_name, self.channel_name)

    def room_update(self, event):
        """
        Событие из group_send несёт только сигнал "что-то изменилось", без
        готового HTML — рендерим фрагмент здесь, на каждое открытое
        соединение отдельно, т.к. _room_status.html зависит от того, ЧЕЙ
        это просмотр (my_room_player/is_player у каждого игрока свои).
        """
        user = self.scope["user"]
        room = Room.objects.prefetch_related("room_players__user", "game_sessions__quiz").filter(
            token=self.room_code
        ).first()
        if room is None:
            return
        context = _get_room_context({"object": room}, room, user)
        html = render_to_string("multiplayer/_room_status.html", context)
        self.send(text_data=html)
