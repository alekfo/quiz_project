from django.db.models import prefetch_related_objects
from django.shortcuts import render
from django.utils.crypto import get_random_string
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction, IntegrityError
from django.http import HttpResponse, HttpRequest, HttpResponseRedirect
from django.urls import reverse

from .models import Room, RoomPlayer


def _generate_room_token():
    return get_random_string(12)

class RoomCreateView(LoginRequiredMixin, CreateView):
    model = Room
    fields = ["title"]

    def get_context_data(self, **kwargs):
        """"
        переопределяем контекст чтобы использовать одну форм и для create и для update
        """
        context = super().get_context_data(**kwargs)
        context.setdefault("page_title", "Room Creating")
        context.setdefault("page_header", "Дайте название комнате")
        context.setdefault("submit_label", "Создать комнату")
        return context

    def form_valid(self, form):
        user = self.request.user
        title = form.cleaned_data["title"]
        try:
            with transaction.atomic():
                room = Room.objects.create(
                    title=title,
                    token=_generate_room_token(),
                    host=user
                )
                room_player = RoomPlayer.objects.create(
                    room=room,
                    user=user
                )
        except IntegrityError:
            with transaction.atomic():
                room = Room.objects.create(
                    title=title,
                    token=_generate_room_token(),
                    host=user
                )
                room_player = RoomPlayer.objects.create(
                    room=room,
                    user=user
                )
        self.object = room
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        """
        при успешной валидации и создании всех необходимых инстансов модели,
        перенаправляем в multiplayer:room_detail
        """
        return reverse("multiplayer:room_detail", kwargs={"code": self.object.token})

class RoomDetailView(LoginRequiredMixin, DetailView):

    model = Room
    slug_field = "token" # какое поле модели искать
    slug_url_kwarg = "code" # как называется параметр в urls.py

    def get_queryset(self):
        return Room.objects.prefetch_related("room_players__user", "game_sessions__quiz")