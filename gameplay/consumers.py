import json

from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from django.template.loader import render_to_string
from django.urls import reverse

from .models import GameSession
from .services import get_answer_stats


class GameSessionConsumer(WebsocketConsumer):

    def connect(self):
        self.session_code = self.scope["url_route"]["kwargs"]["pk"]
        user = self.scope["user"]

        if not user.is_authenticated:
            self.close()
            return

        session = (GameSession.objects
                   .select_related("room")
                   .prefetch_related("participants", "participants__user")
                   .filter(pk=self.session_code)
                   .first())

        if session is None:
            self.close()
            return

        self.is_participant = user in [participant.user for participant in session.participants.all()]
        self.is_host = session.room.host_id == user.id if session.room else False

        if not (self.is_participant or self.is_host):
            self.close()
            return

        self.last_question_id = session.current_question_id
        self.group_name = f"session_{self.session_code}"
        async_to_sync(self.channel_layer.group_add)(self.group_name, self.channel_name)
        self.accept()
        self.session_update({"type": "session.update"})

    def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            async_to_sync(self.channel_layer.group_discard)(self.group_name, self.channel_name)

    def session_update(self, event):

        session = (GameSession.objects
                   .prefetch_related("participants", "participants__user", "participants__participants_answers")
                   .filter(pk=self.session_code)
                   .first())

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

        user = self.scope["user"]
        participant = next((p for p in session.participants.all() if p.user_id == user.id), None)
        if not self.is_participant and not self.is_host:
            return

        # определяем ответил ли уже текущий пользователь
        already_answered = False

        if self.is_participant:
            if session.current_question_id is not None:
                current_answer = next(
                    (a for a in participant.participants_answers.all() if a.question_id == session.current_question_id),
                    None,
                )
                already_answered = bool(current_answer and (current_answer.answered_at or current_answer.is_skipped))

        # определяем роль пользователя
        role = "player" if self.is_participant else "host_observer"

        context = {
            "current_session": session,
            "already_answered": already_answered,
            "role": role,
            "user": user,
        }

        #получаем необходимый контекст подсчета ответтов (participants_count, answered_count, all_answered)
        # из функции get_answer_stats
        answer_context = get_answer_stats(session)
        context.update(answer_context)

        html = render_to_string("gameplay/_players_status.html", context)
        payload = {
            "type": "status",
            "html": html,
            "answered_count": answer_context["answered_count"],
            "participants_count": answer_context["participants_count"],
            "all_answered": answer_context["all_answered"],
            "current_question_id": session.current_question_id,
        }
        self.send(text_data=json.dumps(payload))