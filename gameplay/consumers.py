import json

from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from django.template.loader import render_to_string
from django.urls import reverse

from .models import GameSession


class GameSessionConsumer(WebsocketConsumer):

    def connect(self):
        self.session_code = self.scope["url_route"]["kwargs"]["pk"]
        user = self.scope["user"]

        if not user.is_authenticated:
            self.close()
            return

        session = GameSession.objects.prefetch_related("participants", "participants__user",).filter(pk=self.session_code).first()
        if session is None:
            self.close()
            return

        if user not in [participant.user for participant in session.participants.all()]:
            self.close()
            return

        self.last_question_id = session.current_question_id
        self.group_name = f"session_{self.session_code}"
        async_to_sync(self.channel_layer.group_add)(self.group_name, self.channel_name)
        self.accept()

    def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            async_to_sync(self.channel_layer.group_discard)(self.group_name, self.channel_name)

    def session_update(self, event):

        session = GameSession.objects.prefetch_related("participants", "participants__user", "participants__participants_answers").filter(pk=self.session_code).first()
        if session is None:
            return

        if session.status == "completed":
            self.send(text_data=json.dumps({
                "type": "redirect",
                "url": reverse("gameplay:result", kwargs={"pk": session.pk}),
            }))
            return

        if session.current_question_id != self.last_question_id:
            self.last_question_id = session.current_question_id
            self.send(text_data=json.dumps({
                "type": "redirect",
                "url": reverse("gameplay:play", kwargs={"pk": session.pk}),
            }))
            return

        #определяем ответил ли уже текущий пользователь
        user = self.scope["user"]
        participant = next((p for p in session.participants.all() if p.user_id == user.id), None)
        if participant is None:
            return
        already_answered = False
        if session.current_question is not None:
            current_answer = next(
                (a for a in participant.participants_answers.all() if a.question_id == session.current_question_id),
                None,
            )
            already_answered = bool(current_answer and (current_answer.answered_at or current_answer.is_skipped))

        context = {"current_session": session, "already_answered": already_answered}
        html = render_to_string("gameplay/_players_status.html", context)
        payload = {
            "html": html,
        }
        self.send(text_data=json.dumps(payload))