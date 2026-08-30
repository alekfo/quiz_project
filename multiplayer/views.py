from django.core.exceptions import PermissionDenied
from django.utils.crypto import get_random_string
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction, IntegrityError
from django.http import HttpResponse, HttpRequest, HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.conf import settings

from .models import Room, RoomPlayer
from .forms import RoomQuizForm, RoomPlayerReadyForm


def _generate_room_token():
    return get_random_string(12)

def _get_room_context(context: dict, room: Room, user: settings.AUTH_USER_MODEL) -> dict:
    context["is_host"] = room.host == user
    my_room_player = next(
        (p for p in room.room_players.all() if p.user_id == user.id),
        None,
    )
    context["my_room_player"] = my_room_player
    context["is_player"] = my_room_player is not None
    if context["is_host"]:
        context["quiz_form"] = RoomQuizForm(instance=room, user=user)
    return context

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
        except IntegrityError:
            with transaction.atomic():
                room = Room.objects.create(
                    title=title,
                    token=_generate_room_token(),
                    host=user
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return _get_room_context(context, self.object, self.request.user)

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

@login_required
@require_POST
def room_set_quiz(request: HttpRequest, code: str):
    room = get_object_or_404(Room.objects.prefetch_related("room_players"), token=code)
    if room.host != request.user:
        raise PermissionDenied
    form = RoomQuizForm(request.POST, instance=room, user=request.user)

    if form.is_valid():
        form.save()
        room.room_players.update(is_ready=False)
        messages.success(request, "Квиз выбран, требуется подтверждение готовноти игроков")
    else:
        messages.error(request, "Не удалось выбрать квиз")
    return redirect("multiplayer:room_detail", code=code)

@login_required
@require_POST
def room_confirm_ready(request, code):
    room_player = get_object_or_404(RoomPlayer, room__token=code, user=request.user)
    room_player.is_ready = True
    room_player.save(update_fields=["is_ready"])
    messages.success(request, "Готовность подтверждена")
    return redirect("multiplayer:room_detail", code=code)

@login_required
def room_status(request: HttpRequest, code: str):
    room = get_object_or_404(Room.objects.prefetch_related("room_players"), token=code)

    #Если хостом была нажата кнопка "Начать", то при очередном пуллинге будет редирект на игру у каждого участника
    if room.status == "in_progress" and room.current_game_session_id:
        response = HttpResponse(status=204)  # тело не важно, HX-Redirect всё равно всё решает
        response["HX-Redirect"] = reverse("gameplay:play", kwargs={"pk": room.current_game_session_id})
        return response

    context = {
        "object": room,
    }
    context = _get_room_context(context, room, request.user)
    return render(request, "multiplayer/_room_status.html", context=context)