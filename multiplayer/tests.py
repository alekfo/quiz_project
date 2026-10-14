from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from gameplay.models import GameParticipant, GameSession, SeriesRun
from quiz_project.testing import TEST_SETTINGS, BaseTestCase, make_series, make_user
from quizzes.models import QuizSeries
from social.models import SavedQuizSeries

from .consumers import RoomConsumer
from .forms import RoomSeriesForm
from .models import Room, RoomPlayer


def room_url(name: str, room: Room) -> str:
    return reverse(f"multiplayer:{name}", kwargs={"code": room.token})


class RoomTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.host = make_user("host")
        self.guest = make_user("guest")
        self.room = Room.objects.create(title="Комната", token="roomtoken", host=self.host)
        self.client.force_login(self.host)
        self.guest_client = Client()
        self.guest_client.force_login(self.guest)

    def add_player(self, user, is_ready=False) -> RoomPlayer:
        return RoomPlayer.objects.create(room=self.room, user=user, is_ready=is_ready)

    def make_run(self, series, status="in_progress") -> SeriesRun:
        run = SeriesRun.objects.create(
            series=series, mode="multiplayer", room=self.room, created_by=self.host,
            status=status, current_round_index=0,
        )
        self.room.current_series = series
        self.room.current_series_run = run
        self.room.save()
        return run


class LoginRequiredTests(RoomTestCase):
    def test_all_room_urls_require_login(self):
        self.client.logout()
        get_urls = [reverse("multiplayer:room_list"), reverse("multiplayer:room_create")] + [
            room_url(name, self.room) for name in ("room_detail", "room_join", "room_quit", "room_close")
        ]
        for url in get_urls:
            with self.subTest(url=url):
                self.assertLoginRequired(self.client.get(url))
        for name in ("room_set_quiz", "room_reset_quiz", "room_confirm_ready", "room_start"):
            with self.subTest(name=name):
                self.assertLoginRequired(self.client.post(room_url(name, self.room)))

    def test_state_changing_views_reject_get(self):
        for name in ("room_set_quiz", "room_reset_quiz", "room_confirm_ready", "room_start"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(room_url(name, self.room)).status_code, 405)


class RoomCreateAndListTests(RoomTestCase):
    def test_create_room_sets_host_and_token_but_no_player(self):
        response = self.client.post(reverse("multiplayer:room_create"), {"title": "Новая"})
        room = Room.objects.get(title="Новая")
        self.assertRedirects(response, room_url("room_detail", room), fetch_redirect_response=False)
        self.assertEqual((room.host, room.status, len(room.token)), (self.host, "waiting", 12))
        # хост не становится игроком автоматически - он может быть чистым модератором
        self.assertFalse(room.room_players.exists())

    def test_tokens_are_unique(self):
        for _ in range(5):
            self.client.post(reverse("multiplayer:room_create"), {"title": "Комната"})
        tokens = list(Room.objects.values_list("token", flat=True))
        self.assertEqual(len(tokens), len(set(tokens)))

    def test_list_shows_only_own_rooms_split_by_status(self):
        finished = Room.objects.create(title="Закрытая", token="finished1", host=self.host, status="finished")
        Room.objects.create(title="Чужая", token="foreign1", host=self.guest)
        response = self.client.get(reverse("multiplayer:room_list"))
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(response.context["active_rooms"], [self.room])
        self.assertCountEqual(response.context["finished_rooms"], [finished])


class RoomDetailTests(RoomTestCase):
    def detail(self, client):
        return client.get(room_url("room_detail", self.room))

    def test_context_flags_for_host_player_and_visitor(self):
        self.add_player(self.guest)
        visitor = Client()
        visitor.force_login(make_user("visitor"))
        cases = [(self.client, True, False), (self.guest_client, False, True), (visitor, False, False)]
        for client, is_host, is_player in cases:
            with self.subTest(is_host=is_host, is_player=is_player):
                response = self.detail(client)
                self.assertEqual(response.status_code, 200)
                self.assertEqual((response.context["is_host"], response.context["is_player"]),
                                 (is_host, is_player))

    def test_series_form_is_only_in_host_context(self):
        self.add_player(self.guest)
        self.assertIn("series_form", self.detail(self.client).context)
        self.assertNotIn("series_form", self.detail(self.guest_client).context)

    def test_can_start_round_depends_on_series_and_active_round(self):
        series = make_series(self.host)
        context = self.detail(self.client).context
        self.assertEqual((context["has_selected_series"], context["can_start_round"], context["is_first_round"]),
                         (False, False, True))

        run = self.make_run(series)
        context = self.detail(self.client).context
        self.assertEqual((context["has_selected_series"], context["can_start_round"], context["is_first_round"]),
                         (True, True, False))

        session = GameSession.objects.create(
            quiz=series.rounds.get(), mode="multiplayer", created_by=self.host, room=self.room, series_run=run,
        )
        self.room.current_game_session = session
        self.room.save()
        self.assertFalse(self.detail(self.client).context["can_start_round"])

    def test_history_contains_only_finished_runs(self):
        abandoned = SeriesRun.objects.create(
            series=make_series(self.host, title="A"), mode="multiplayer", room=self.room,
            created_by=self.host, status="abandoned",
        )
        completed = SeriesRun.objects.create(
            series=make_series(self.host, title="B"), mode="multiplayer", room=self.room,
            created_by=self.host, status="completed",
        )
        self.make_run(make_series(self.host, title="C"))
        self.assertCountEqual(self.detail(self.client).context["completed_series_run"], [abandoned, completed])

    def test_finished_room_still_renders(self):
        Room.objects.filter(pk=self.room.pk).update(status="finished")
        self.assertEqual(self.detail(self.client).status_code, 200)

    def test_unknown_room_is_404(self):
        response = self.client.get(reverse("multiplayer:room_detail", kwargs={"code": "nosuchroom"}))
        self.assertEqual(response.status_code, 404)


class JoinAndQuitTests(RoomTestCase):
    def test_join_creates_room_player(self):
        response = self.guest_client.get(room_url("room_join", self.room))
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        player = RoomPlayer.objects.get()
        self.assertEqual((player.room, player.user, player.is_ready), (self.room, self.guest, False))

    def test_join_twice_keeps_single_player(self):
        self.guest_client.get(room_url("room_join", self.room))
        self.guest_client.get(room_url("room_join", self.room))
        self.assertEqual(RoomPlayer.objects.count(), 1)

    def test_cannot_join_second_active_room(self):
        self.add_player(self.guest)
        other = Room.objects.create(title="Другая", token="other1", host=self.host)
        self.guest_client.get(room_url("room_join", other))
        self.assertFalse(other.room_players.exists())

    def test_can_join_after_previous_room_finished(self):
        self.add_player(self.guest)
        Room.objects.filter(pk=self.room.pk).update(status="finished")
        other = Room.objects.create(title="Другая", token="other1", host=self.host)
        self.guest_client.get(room_url("room_join", other))
        self.assertTrue(other.room_players.filter(user=self.guest).exists())

    def test_cannot_join_finished_room(self):
        Room.objects.filter(pk=self.room.pk).update(status="finished")
        self.guest_client.get(room_url("room_join", self.room))
        self.assertFalse(RoomPlayer.objects.exists())

    def test_join_unknown_room_is_404(self):
        response = self.guest_client.get(reverse("multiplayer:room_join", kwargs={"code": "nosuchroom"}))
        self.assertEqual(response.status_code, 404)

    def test_quit_removes_only_own_player(self):
        self.add_player(self.guest)
        self.add_player(self.host)
        response = self.guest_client.post(room_url("room_quit", self.room))
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        self.assertEqual(list(self.room.room_players.values_list("user", flat=True)), [self.host.pk])

    def test_quit_by_non_player_is_404(self):
        self.add_player(self.host)
        self.assertEqual(self.guest_client.post(room_url("room_quit", self.room)).status_code, 404)
        self.assertEqual(RoomPlayer.objects.count(), 1)


class SeriesSelectionTests(RoomTestCase):
    """Выбор серии хостом: правило available_to (свои + сохранённые публичные)."""

    def setUp(self):
        super().setUp()
        self.author = make_user("author")
        self.own = make_series(self.host, status="private", title="Своя")
        self.foreign_public = make_series(self.author, status="public", title="Чужая публичная")
        self.foreign_private = make_series(self.author, status="private", title="Чужая приватная")

    def select(self, series, client=None):
        return (client or self.client).post(room_url("room_set_quiz", self.room), {"current_series": series.pk})

    def current_series(self):
        self.room.refresh_from_db()
        return self.room.current_series

    def test_form_offers_only_own_and_saved_public_series(self):
        saved = make_series(self.author, status="public", title="Сохранённая")
        SavedQuizSeries.objects.create(user=self.host, series=saved)
        form = RoomSeriesForm(instance=self.room, user=self.host)
        self.assertCountEqual(form.fields["current_series"].queryset, [self.own, saved])

    def test_host_selects_own_series(self):
        response = self.select(self.own)
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        self.assertEqual(self.current_series(), self.own)

    def test_host_selects_saved_public_series(self):
        SavedQuizSeries.objects.create(user=self.host, series=self.foreign_public)
        self.select(self.foreign_public)
        self.assertEqual(self.current_series(), self.foreign_public)

    def test_unsaved_foreign_public_series_is_rejected(self):
        """pk, подставленный в POST вручную, отклоняется валидацией ModelChoiceField."""
        self.select(self.foreign_public)
        self.assertIsNone(self.current_series())

    def test_foreign_private_series_is_rejected_even_if_saved_earlier(self):
        SavedQuizSeries.objects.create(user=self.host, series=self.foreign_private)
        self.select(self.foreign_private)
        self.assertIsNone(self.current_series())

    def test_only_host_can_select_or_reset(self):
        self.add_player(self.guest)
        own_of_guest = make_series(self.guest)
        self.assertEqual(self.select(own_of_guest, client=self.guest_client).status_code, 403)
        self.assertEqual(self.guest_client.post(room_url("room_reset_quiz", self.room)).status_code, 403)
        self.assertIsNone(self.current_series())

    def test_selecting_series_resets_readiness(self):
        self.add_player(self.guest, is_ready=True)
        self.select(self.own)
        self.assertFalse(self.room.room_players.filter(is_ready=True).exists())

    def test_selecting_another_series_abandons_current_run(self):
        run = self.make_run(make_series(self.host, title="Старая"))
        self.select(self.own)
        run.refresh_from_db()
        self.room.refresh_from_db()
        self.assertEqual(run.status, "abandoned")
        self.assertIsNotNone(run.finished_at)
        self.assertEqual((self.room.current_series, self.room.current_series_run), (self.own, None))

    def test_reset_clears_series_abandons_run_and_resets_readiness(self):
        run = self.make_run(self.own)
        self.add_player(self.guest, is_ready=True)
        response = self.client.post(room_url("room_reset_quiz", self.room))
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        run.refresh_from_db()
        self.room.refresh_from_db()
        self.assertEqual(run.status, "abandoned")
        self.assertIsNotNone(run.finished_at)
        self.assertEqual((self.room.current_series, self.room.current_series_run), (None, None))
        self.assertFalse(self.room.room_players.filter(is_ready=True).exists())

    def test_series_stays_in_room_when_author_makes_it_private(self):
        """Принятое решение: правило проверяется только в момент выбора, комната доигрывает."""
        SavedQuizSeries.objects.create(user=self.host, series=self.foreign_public)
        self.select(self.foreign_public)
        QuizSeries.objects.filter(pk=self.foreign_public.pk).update(status="private")
        self.assertEqual(self.current_series(), self.foreign_public)
        self.assertEqual(self.client.get(room_url("room_detail", self.room)).status_code, 200)


class ConfirmReadyTests(RoomTestCase):
    def test_player_confirms_readiness(self):
        player = self.add_player(self.guest)
        other = self.add_player(self.host)
        response = self.guest_client.post(room_url("room_confirm_ready", self.room))
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        player.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(player.is_ready)
        self.assertFalse(other.is_ready)

    def test_non_player_gets_404(self):
        self.assertEqual(self.guest_client.post(room_url("room_confirm_ready", self.room)).status_code, 404)


class RoomStartTests(RoomTestCase):
    def setUp(self):
        super().setUp()
        self.series = make_series(self.host, rounds=2, questions=2)
        self.room.current_series = self.series
        self.room.save()

    def start(self, client=None):
        return (client or self.client).post(room_url("room_start", self.room))

    def assertNotStarted(self, response):
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        self.assertFalse(GameSession.objects.exists())
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "waiting")

    def test_only_host_can_start(self):
        self.add_player(self.guest, is_ready=True)
        self.assertEqual(self.start(self.guest_client).status_code, 403)
        self.assertFalse(GameSession.objects.exists())

    def test_not_started_without_series(self):
        self.add_player(self.guest, is_ready=True)
        self.room.current_series = None
        self.room.save()
        self.assertNotStarted(self.start())

    def test_not_started_without_players(self):
        self.assertNotStarted(self.start())

    def test_not_started_until_everyone_is_ready(self):
        self.add_player(self.guest, is_ready=True)
        self.add_player(self.host, is_ready=False)
        self.assertNotStarted(self.start())

    def test_not_started_for_series_without_rounds(self):
        self.add_player(self.guest, is_ready=True)
        self.room.current_series = make_series(self.host, rounds=0, title="Пустая")
        self.room.save()
        self.assertNotStarted(self.start())
        self.assertFalse(SeriesRun.objects.exists())

    def test_not_started_when_host_already_plays_this_series_elsewhere(self):
        """Частичный UniqueConstraint: один активный прогон серии на пользователя."""
        self.add_player(self.guest, is_ready=True)
        SeriesRun.objects.create(series=self.series, mode="solo", created_by=self.host, current_round_index=0)
        self.assertNotStarted(self.start())
        self.assertEqual(SeriesRun.objects.count(), 1)

    def test_start_creates_run_session_and_participants(self):
        self.add_player(self.host, is_ready=True)
        self.add_player(self.guest, is_ready=True)
        response = self.start()

        session = GameSession.objects.get()
        run = SeriesRun.objects.get()
        first_round = self.series.rounds.get(round_order=0)
        # хост-игрок уходит в игру
        self.assertRedirects(
            response, reverse("gameplay:play", kwargs={"pk": session.pk}), fetch_redirect_response=False,
        )
        self.assertEqual((run.series, run.mode, run.room, run.created_by, run.current_round_index),
                         (self.series, "multiplayer", self.room, self.host, 0))
        self.assertEqual(
            (session.quiz, session.mode, session.room, session.series_run, session.current_question),
            (first_round, "multiplayer", self.room, run, first_round.questions.get(order=0)),
        )
        self.assertCountEqual(session.participants.values_list("user", flat=True), [self.host.pk, self.guest.pk])
        self.room.refresh_from_db()
        self.assertEqual((self.room.status, self.room.current_game_session, self.room.current_series_run),
                         ("in_progress", session, run))

    def test_host_moderator_stays_in_lobby(self):
        self.add_player(self.guest, is_ready=True)
        response = self.start()
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        self.assertEqual(GameParticipant.objects.count(), 1)

    def test_second_start_does_not_create_second_session(self):
        self.add_player(self.host, is_ready=True)
        self.start()
        session = GameSession.objects.get()
        response = self.start()
        self.assertRedirects(
            response, reverse("gameplay:play", kwargs={"pk": session.pk}), fetch_redirect_response=False,
        )
        self.assertEqual(GameSession.objects.count(), 1)
        self.assertEqual(SeriesRun.objects.count(), 1)

    def test_start_with_completed_run_is_forbidden(self):
        self.add_player(self.guest, is_ready=True)
        self.make_run(self.series, status="completed")
        self.assertEqual(self.start().status_code, 403)
        self.assertFalse(GameSession.objects.exists())


class RoomCloseTests(RoomTestCase):
    def test_host_sees_confirmation_page(self):
        self.assertEqual(self.client.get(room_url("room_close", self.room)).status_code, 200)

    def test_host_closes_room_and_unfinished_run_becomes_abandoned(self):
        run = self.make_run(make_series(self.host))
        response = self.client.post(room_url("room_close", self.room))
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        self.room.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(self.room.status, "finished")
        # недоигранная серия - "abandoned", а не "completed"
        self.assertEqual(run.status, "abandoned")
        self.assertIsNotNone(run.finished_at)

    def test_non_host_cannot_close(self):
        self.add_player(self.guest)
        self.assertEqual(self.guest_client.post(room_url("room_close", self.room)).status_code, 403)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, "waiting")

    def test_cannot_close_while_round_is_running(self):
        series = make_series(self.host)
        run = self.make_run(series)
        session = GameSession.objects.create(
            quiz=series.rounds.get(), mode="multiplayer", created_by=self.host, room=self.room, series_run=run,
        )
        self.room.current_game_session = session
        self.room.status = "in_progress"
        self.room.save()
        response = self.client.post(room_url("room_close", self.room))
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        self.room.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual((self.room.status, run.status), ("in_progress", "in_progress"))

    def test_closed_room_cannot_be_closed_again(self):
        Room.objects.filter(pk=self.room.pk).update(status="finished")
        self.assertEqual(self.client.post(room_url("room_close", self.room)).status_code, 403)


@override_settings(**TEST_SETTINGS)
class RoomConsumerTests(TransactionTestCase):
    """WebSocket лобби: кого пускаем и что шлём на room.update."""

    def setUp(self):
        self.host = make_user("host")
        self.guest = make_user("guest")
        self.series = make_series(self.host, rounds=1)
        self.room = Room.objects.create(title="Комната", token="roomtoken", host=self.host)
        RoomPlayer.objects.create(room=self.room, user=self.guest)

    async def connect(self, user, code=None):
        code = code or self.room.token
        communicator = WebsocketCommunicator(RoomConsumer.as_asgi(), f"/ws/multiplayer/rooms/{code}/")
        communicator.scope["user"] = user
        communicator.scope["url_route"] = {"kwargs": {"code": code}}
        connected, _ = await communicator.connect()
        return communicator, connected

    async def send_update(self):
        await get_channel_layer().group_send(f"room_{self.room.token}", {"type": "room.update"})

    async def test_only_host_and_players_can_connect(self):
        stranger = await sync_to_async(make_user)("stranger")
        cases = [
            (AnonymousUser(), None, False),
            (stranger, None, False),
            (self.host, "nosuchroom", False),
            (self.host, None, True),
            (self.guest, None, True),
        ]
        for user, code, expected in cases:
            with self.subTest(user=str(user), code=code):
                communicator, connected = await self.connect(user, code)
                self.assertEqual(connected, expected)
                await communicator.disconnect()

    async def test_update_sends_lobby_fragment_with_flags(self):
        await Room.objects.filter(pk=self.room.pk).aupdate(current_series=self.series)
        communicator, _ = await self.connect(self.guest)
        await self.send_update()
        message = await communicator.receive_json_from()
        self.assertCountEqual(
            message, ["html", "can_confirm", "is_ready", "can_start_round", "has_selected_series", "is_first_round"],
        )
        # серия выбрана, игрок ещё не подтвердил готовность
        self.assertEqual(
            (message["can_confirm"], message["is_ready"], message["has_selected_series"], message["is_first_round"]),
            (True, False, True, True),
        )
        await communicator.disconnect()

    async def start_round(self):
        session = await GameSession.objects.acreate(
            quiz=await self.series.rounds.aget(), mode="multiplayer", created_by=self.host, room=self.room,
        )
        await Room.objects.filter(pk=self.room.pk).aupdate(status="in_progress", current_game_session=session)
        return session

    async def test_started_round_redirects_player_into_game(self):
        communicator, _ = await self.connect(self.guest)
        session = await self.start_round()
        await self.send_update()
        self.assertEqual(
            await communicator.receive_json_from(),
            {"type": "redirect", "url": reverse("gameplay:play", kwargs={"pk": session.pk})},
        )
        await communicator.disconnect()

    async def test_started_round_does_not_redirect_host_moderator(self):
        """Хост без RoomPlayer не участник GameSession - редирект в игру дал бы ему 403."""
        communicator, _ = await self.connect(self.host)
        await self.start_round()
        await self.send_update()
        message = await communicator.receive_json_from()
        self.assertIn("html", message)
        self.assertNotEqual(message.get("type"), "redirect")
        await communicator.disconnect()
