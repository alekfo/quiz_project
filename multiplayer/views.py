
from django.utils.crypto import get_random_string
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction, IntegrityError
from django.http import HttpResponse, HttpRequest, HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required

from .models import Room, RoomPlayer


def _generate_room_token():
    return get_random_string(12)

class RoomListView(LoginRequiredMixin, ListView):

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_rooms"] = self.object_list.exclude(status="finished")
        context["finished_rooms"] = self.object_list.filter(status="finished")
        return context

    def get_queryset(self):
        return Room.objects.filter(host=self.request.user).order_by("-created_at")

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
                # room_player = RoomPlayer.objects.create(
                #     room=room,
                #     user=user
                # )
        except IntegrityError:
            with transaction.atomic():
                room = Room.objects.create(
                    title=title,
                    token=_generate_room_token(),
                    host=user
                )
                # room_player = RoomPlayer.objects.create(
                #     room=room,
                #     user=user
                # )
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_host"] = self.object.host == self.request.user
        context["is_player"] = self.request.user in [player.user for player in self.object.room_players.all()]
        return context

    def get_queryset(self):
        return Room.objects.prefetch_related("room_players__user", "game_sessions__quiz")

@login_required
def room_join(request: HttpRequest, code: str):
    # проверяем, не участвует ли пользователь УЖЕ в какой-то комнате
    active_room = Room.objects.filter(
        room_players__user=request.user,
        status__in=["waiting", "in_progress"]
    ).first()
    if active_room:
        # active_room_link = f"{request.scheme}://{request.get_host()}/multiplayer/rooms/{active_room.token}/"
        active_room_link = request.build_absolute_uri(
            reverse("multiplayer:room_detail", kwargs={"code": active_room.token})
        )
        messages.error(request, f"Вы уже являетесь участником другой активной комнаты: {active_room_link}")
        return redirect("multiplayer:room_detail", code=code)

    # если активных сессий не найдено, проверяем, не является ли текущая комната завершенной
    room = get_object_or_404(Room, token=code)
    if room.status == "finished":
        messages.error(request, "Комната уже не активна")
        return redirect("multiplayer:room_detail", code=code)

    # если все окей - регистрируем пользователя в комнате как участника
    try:
        RoomPlayer.objects.create(room=room, user=request.user)
    except IntegrityError:
        messages.error(request, "Вы уже участвуете в этой комнате")
    url = reverse("multiplayer:room_detail", kwargs={"code": code})
    return redirect(url)

class RoomPlayerDeleteView(LoginRequiredMixin, DeleteView):
    model = RoomPlayer

    def get_object(self, queryset = None):
        return get_object_or_404(
            RoomPlayer.objects.select_related("room", "user"),
            room__token=self.kwargs.get("code"),
            user=self.request.user
        )

    def get_success_url(self):
        return reverse("multiplayer:room_detail", kwargs={"code": self.kwargs.get("code")})