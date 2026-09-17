import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
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
from django.utils import timezone

from .models import Room, RoomPlayer
from .forms import RoomSeriesForm, RoomPlayerReadyForm
from gameplay.models import GameSession, GameParticipant, SeriesRun
from gameplay.services import get_series_progress

logger = logging.getLogger(__name__)

def _generate_room_token():
    return get_random_string(12)

def _notify_room(room: Room) -> None:
    """
    Сигнал "что-то в комнате изменилось" всем открытым WebSocket-соединениям
    этой комнаты (см. multiplayer.consumers.RoomConsumer.room_update) — сам
    HTML не передаём, каждый подключённый рендерит фрагмент под себя.
    """
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"room_{room.token}",
        {"type": "room.update"},
    )

def _get_room_context(context: dict, room: Room, user: settings.AUTH_USER_MODEL) -> dict:
    context["is_host"] = room.host == user
    my_room_player = next(
        (p for p in room.room_players.all() if p.user_id == user.id),
        None,
    )
    #является ли пользователь игроком в комнате
    context["my_room_player"] = my_room_player
    context["is_player"] = my_room_player is not None

    #передаем форму для выбора серии
    if context["is_host"]:
        context["series_form"] = RoomSeriesForm(instance=room, user=user)

    context["is_first_round"] = room.current_series_run is None

    #получаем данные для отображения прогресса current_series_run
    if room.current_series_run:
        series_progress = get_series_progress(room.current_series_run)
        context.update(series_progress)


    #флаг о том, выбрана ли хостом серия
    context["has_selected_series"] = room.current_series is not None

    #флаг о том, может ли пользователь стартовать раунд
    context["can_start_round"] = room.current_series is not None and (room.current_series_run is None or room.current_series_run.status != "completed") and room.current_game_session is None

    #завершенные series_run
    completed_series_run = [
        sr
        for sr in room.series_runs.all()
        if sr.status in ["completed", "abandoned"]
    ]
    context["completed_series_run"] = completed_series_run

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
        return Room.objects.prefetch_related("room_players__user", "game_sessions__quiz", "series_runs")

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
        with transaction.atomic():
            RoomPlayer.objects.create(room=room, user=request.user)
            # on_commit, а не прямой вызов: WS-консьюмер (RoomConsumer)
            # читает Room из БД через отдельное соединение — без on_commit
            # он может успеть выполнить запрос раньше, чем эта транзакция
            # закоммитится, и не увидеть только что созданного RoomPlayer.
            # С on_commit колбэк _notify_room откладывается и реально
            # выполняется только после успешного коммита этой транзакции.
            transaction.on_commit(lambda: _notify_room(room))
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

    def form_valid(self, form):
        room = self.object.room
        success_url = self.get_success_url()
        with transaction.atomic():
            self.object.delete()
            # on_commit: без него WS-консьюмер может прочитать Room ещё до
            # коммита и увидеть уже удалённого RoomPlayer как существующего
            # (или не увидеть только что удалённого — в зависимости от
            # таймингов); on_commit гарантирует, что _notify_room выполнится
            # только после того, как удаление реально закоммитится.
            transaction.on_commit(lambda: _notify_room(room))
        return HttpResponseRedirect(success_url)

    def get_success_url(self):
        return reverse("multiplayer:room_detail", kwargs={"code": self.kwargs.get("code")})

@login_required
@require_POST
def room_select_series (request: HttpRequest, code: str):
    room = get_object_or_404(Room.objects.prefetch_related("room_players"), token=code)
    if room.host != request.user:
        raise PermissionDenied
    form = RoomSeriesForm(request.POST, instance=room, user=request.user)

    if form.is_valid():
        with transaction.atomic():
            form.save()
            #Практическое правило: если related_name стоит на чужой модели и указывает на текущую (обратная связь) — доступ через него даёт менеджер, .update()/.filter() работают.
            #Если поле ForeignKey объявлено прямо на этой модели (прямая связь) — доступ даёт инстанс, только .save(). room.game_sessions (обратнаясвязь от GameSession.room) — тоже менеджер, тоже можно .update().
            #А room.current_series, room.current_series_run, room.current_game_session, room.host — все прямые FK на Room, все дают инстанс.
            if room.current_series_run:
                room.current_series_run.status = "abandoned"
                room.current_series_run.finished_at = timezone.now()
                room.current_series_run.save(update_fields=["status", "finished_at"])
                room.current_series_run = None
                room.save(update_fields=["current_series_run"])
            room.room_players.update(is_ready=False)
            # on_commit: без него WS-консьюмер может прочитать Room раньше,
            # чем эта транзакция закоммитится, и отдать подключённым старый
            # current_quiz/is_ready. on_commit откладывает _notify_room до
            # момента, когда изменения уже гарантированно видны из БД.
            transaction.on_commit(lambda: _notify_room(room))
        messages.success(request, "Квиз выбран, требуется подтверждение готовноти игроков")
    else:
        messages.error(request, "Не удалось выбрать квиз")
    return redirect("multiplayer:room_detail", code=code)

@login_required
@require_POST
def room_reset_series(request: HttpRequest, code: str):
    room = get_object_or_404(Room.objects.prefetch_related("room_players"), token=code)
    if room.host != request.user:
        raise PermissionDenied
    with transaction.atomic():

        if room.current_series_run:
            room.current_series_run.status = "abandoned"
            room.current_series_run.finished_at = timezone.now()
            room.current_series_run.save(update_fields=["status", "finished_at"])
            room.current_series_run = None
        room.current_series = None
        room.save(update_fields=["current_series", "current_series_run"])
        room.room_players.update(is_ready=False)
        # on_commit: та же причина, что и в room_set_quiz — без него
        # WS-консьюмер может прочитать Room до коммита и отдать
        # подключённым ещё не сброшенный current_quiz/is_ready.
        transaction.on_commit(lambda: _notify_room(room))
    messages.success(request, "Квиз сброшен. Можете выбрать другой")
    return redirect("multiplayer:room_detail", code=code)

@login_required
@require_POST
def room_confirm_ready(request, code):
    with transaction.atomic():
        room_player = get_object_or_404(RoomPlayer, room__token=code, user=request.user)
        room_player.is_ready = True
        room_player.save(update_fields=["is_ready"])
        # on_commit: без него WS-консьюмер может прочитать Room раньше,
        # чем эта транзакция закоммитится, и отдать подключённым ещё
        # не подтверждённую готовность этого игрока.
        transaction.on_commit(lambda: _notify_room(room_player.room))
    messages.success(request, "Готовность подтверждена")
    return redirect("multiplayer:room_detail", code=code)

@login_required
@require_POST
def room_start(request: HttpRequest, code: str):
    room = get_object_or_404(Room.objects.select_related("current_series", "current_series_run").prefetch_related("room_players", "current_series__rounds__questions", "current_series_run__game_sessions"), token=code)
    user = request.user

    if room.host != user:
        raise PermissionDenied

    if room.current_series is None:
        messages.error(request, "Не выбран квиз")
        url = reverse("multiplayer:room_detail", kwargs={"code": code})
        return redirect(url)

    if not room.room_players.exists():
        messages.error(request, "Для начала игры необходим хотя бы один участник")
        url = reverse("multiplayer:room_detail", kwargs={"code": code})
        return redirect(url)

    if any([not player.is_ready for player in room.room_players.all()]):
        messages.error(request, "Не все участники комнаты подтвердили готовность")
        url = reverse("multiplayer:room_detail", kwargs={"code": code})
        return redirect(url)

    if not room.current_series.rounds.exists():
        messages.error(request, "Выбранный квиз не содержит ни одного раунда. Попробуйте другой")
        url = reverse("multiplayer:room_detail", kwargs={"code": code})
        return redirect(url)

    if room.current_series_run:
        #серияуже пройдена - ошибка
        if room.current_series_run.status == "completed":
            raise PermissionDenied
        #у current_series_run уже есть активная game_session - переходим в нее и доигрываем
        for game_session in room.current_series_run.game_sessions.all():
            if game_session.status == "in_progress":
                url = reverse("gameplay:play", kwargs={"pk": game_session.pk})
                return redirect(url)

    #проверяем на наличие IntegrityError в транзакции, если было - сессия in_progress уже существует, забираем ее и идем на gameplay:play
    with transaction.atomic():
        current_series_run = room.current_series_run
        if current_series_run is None:
            first_round = room.current_series.rounds.order_by("round_order").first()
            try:
                current_series_run = SeriesRun.objects.create(
                    series=room.current_series,
                    mode="multiplayer",
                    room=room,
                    created_by=user,
                    current_round_index=first_round.round_order
                )
            except IntegrityError:
                messages.error(request, "Вы уже проходите эту серию в другом месте")
                return redirect("multiplayer:room_detail", code=code)
            # сохраняем тут, т.к в определении current_round нам нужно точно чтобы current_round_index сохранился в current_series_run
            room.current_series_run = current_series_run
            room.save(update_fields=["current_series_run"])

        current_round = next((current_round for current_round in room.current_series.rounds.all() if
                              current_round.round_order == current_series_run.current_round_index), None)
        if current_round is None:
            messages.error(request, "Раунд не найден — возможно, был удалён. Обратитесь к организатору")
            return redirect("multiplayer:room_detail", code=code)

        try:
            session = GameSession.objects.create(
                quiz=current_round,
                mode="multiplayer",
                created_by=user,
                current_question=current_round.questions.first(),
                room=room,
                series_run=current_series_run
            )
        except IntegrityError:
            # настоящий двойной клик по "Начать раунд" — конкурентный запрос
            # уже создал GameSession для этого quiz+user
            session = GameSession.objects.get(quiz=current_round, created_by=user, status="in_progress")
        else:
            for participant in room.room_players.all():
                GameParticipant.objects.create(
                    session=session,
                    user=participant.user
                )
            room.current_game_session = session
            room.current_series_run = current_series_run
            room.status = "in_progress"
            room.save(update_fields=["current_game_session", "status"])
            # on_commit: без него WS-консьюмер (и RoomConsumer.room_update,
            # который именно по status=="in_progress"+current_game_session_id
            # решает слать редирект в игру) может прочитать Room раньше,
            # чем эта транзакция закоммитится, и не увидеть ни новый
            # status, ни созданную GameSession/GameParticipant.
            transaction.on_commit(lambda: _notify_room(room))
            logger.info("Сессия %s квиза %s серии %s создана и начата пользователем %s", session.pk, current_round.pk, room.current_series.pk, session.created_by.username)
    url = reverse("gameplay:play", kwargs={"pk": session.pk})
    return redirect(url)
