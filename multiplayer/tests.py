from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
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

    def grow_lobby(self, run, step):
        """
        Ещё игрок, старый прогон и сыгранный раунд текущей серии - для тестов на число запросов.
        Первый шаг делается до базового замера: пока связанных строк нет, Django пропускает
        вложенные prefetch-запросы, и разница была бы не N+1, а фиксированной.
        """
        self.add_player(make_user(f"player{step}"), is_ready=True)
        SeriesRun.objects.create(
            series=make_series(self.host, title=f"Старая {step}"), mode="multiplayer", room=self.room,
            created_by=self.host, status="abandoned",
        )
        GameSession.objects.create(
            quiz=run.series.rounds.get(round_order=step), mode="multiplayer", created_by=self.host,
            room=self.room, series_run=run, status="completed",
        )
        run.current_round_index = step + 1
        run.save()


class LoginRequiredTests(RoomTestCase):
    def test_all_room_urls_require_login(self):
        self.client.logout()
        get_urls = [reverse("multiplayer:room_list"), reverse("multiplayer:room_create")] + [
            room_url(name, self.room) for name in ("room_detail", "room_quit", "room_reset_quiz", "room_close")
        ]
        for url in get_urls:
            with self.subTest(url=url):
                self.assertLoginRequired(self.client.get(url))
        for name in ("room_join", "room_set_quiz", "room_reset_quiz", "room_confirm_ready", "room_start"):
            with self.subTest(name=name):
                self.assertLoginRequired(self.client.post(room_url(name, self.room)))

    def test_state_changing_views_reject_get(self):
        # GET на ссылку из <img src> на чужой странице не должен ничего менять (CSRF)
        for name in ("room_join", "room_set_quiz", "room_confirm_ready", "room_start"):
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


class RoomListTests(RoomTestCase):
    """Список комнат: комната участника отдельно, комнаты хоста, завершённые (хост или участник)."""

    def room_list(self, client=None):
        response = (client or self.client).get(reverse("multiplayer:room_list"))
        self.assertEqual(response.status_code, 200)
        return response.context

    def test_host_without_playing_sees_room_in_hosted_only(self):
        ctx = self.room_list()
        self.assertIsNone(ctx["participant_room"])
        self.assertCountEqual(ctx["hosted_rooms"], [self.room])
        self.assertCountEqual(ctx["finished_rooms"], [])

    def test_player_of_foreign_room_sees_it_as_participant_room(self):
        self.add_player(self.guest)
        ctx = self.room_list(self.guest_client)
        self.assertEqual(ctx["participant_room"], self.room)
        self.assertCountEqual(ctx["hosted_rooms"], [])

    def test_hosting_and_playing_room_is_not_duplicated_in_hosted(self):
        self.add_player(self.host)
        other = Room.objects.create(title="Вторая", token="hosted2", host=self.host)
        ctx = self.room_list()
        self.assertEqual(ctx["participant_room"], self.room)
        self.assertCountEqual(ctx["hosted_rooms"], [other])

    def test_participant_room_counts_all_players(self):
        # условие "я участник" не должно сужать JOIN, по которому считается players_count
        self.add_player(self.guest)
        self.add_player(make_user("third"))
        ctx = self.room_list(self.guest_client)
        self.assertEqual(ctx["participant_room"].players_count, 2)

    def test_finished_rooms_include_hosted_and_played_without_duplicates(self):
        self.room.status = "finished"
        self.room.save()
        for name in ("p1", "p2", "p3"):
            self.add_player(make_user(name))
        played = Room.objects.create(title="Чужая закрытая", token="played1", host=self.guest, status="finished")
        RoomPlayer.objects.create(room=played, user=self.host)
        RoomPlayer.objects.create(room=played, user=self.guest)

        finished = list(self.room_list()["finished_rooms"])
        self.assertCountEqual(finished, [self.room, played])
        counts = {room.pk: room.players_count for room in finished}
        self.assertEqual(counts, {self.room.pk: 3, played.pk: 2})

    def test_finished_room_is_not_active(self):
        self.add_player(self.host)
        self.room.status = "finished"
        self.room.save()
        ctx = self.room_list()
        self.assertIsNone(ctx["participant_room"])
        self.assertCountEqual(ctx["hosted_rooms"], [])
        self.assertCountEqual(ctx["finished_rooms"], [self.room])

    def test_foreign_rooms_are_not_listed(self):
        Room.objects.create(title="Чужая", token="foreign1", host=self.guest)
        Room.objects.create(title="Чужая закрытая", token="foreign2", host=self.guest, status="finished")
        ctx = self.room_list()
        self.assertIsNone(ctx["participant_room"])
        self.assertCountEqual(ctx["hosted_rooms"], [self.room])
        self.assertCountEqual(ctx["finished_rooms"], [])

    def test_rooms_are_newest_first(self):
        newer = Room.objects.create(title="Новее", token="newer1", host=self.host)
        self.assertEqual(list(self.room_list()["hosted_rooms"]), [newer, self.room])

    def test_query_count_does_not_grow_with_rooms(self):
        series = make_series(self.host)
        self.room.current_series = series
        self.room.save()
        self.add_player(self.host)
        url = reverse("multiplayer:room_list")
        with CaptureQueriesContext(connection) as few:
            self.client.get(url)
        for i in range(3):
            room = Room.objects.create(title=f"Ещё {i}", token=f"more{i}", host=self.host, current_series=series)
            RoomPlayer.objects.create(room=room, user=self.guest)
            Room.objects.create(title=f"Закрытая {i}", token=f"closed{i}", host=self.host,
                                current_series=series, status="finished")
        with CaptureQueriesContext(connection) as many:
            self.client.get(url)
        self.assertEqual(len(many), len(few))


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

    def test_visitor_sees_players_and_series_without_forms_or_links(self):
        """Посетитель: снимок без WS, только вход в комнату; ссылки на результаты и историю ему дали бы 403."""
        series = make_series(self.host, rounds=2, title="Выбранный квиз")
        run = self.make_run(series)
        GameSession.objects.create(
            quiz=series.rounds.get(round_order=0), mode="multiplayer", created_by=self.host,
            room=self.room, series_run=run, status="completed",
        )
        SeriesRun.objects.create(
            series=make_series(self.host, title="Старый"), mode="multiplayer", room=self.room,
            created_by=self.host, status="abandoned",
        )
        self.add_player(self.guest)
        visitor = Client()
        visitor.force_login(make_user("visitor"))

        response = self.detail(visitor)
        self.assertContains(response, "Выбранный квиз")
        self.assertContains(response, "guest")
        self.assertContains(response, room_url("room_join", self.room))
        for absent in ("data-lobby-socket", "confirm-ready-form", "start-form", "select-series-form",
                       "reset-series-form", "lobby-history-details", "/result/", "solo_room"):
            with self.subTest(absent=absent):
                self.assertNotContains(response, absent)

    def test_series_form_is_only_in_host_context(self):
        self.add_player(self.guest)
        self.assertIn("series_form", self.detail(self.client).context)
        self.assertNotIn("series_form", self.detail(self.guest_client).context)

    def start_flags(self):
        context = self.detail(self.client).context
        return {key: context[key] for key in ("players_count", "ready_count", "start_available", "can_start_round")}

    def test_start_is_unavailable_without_series(self):
        self.add_player(self.guest, is_ready=True)
        context = self.detail(self.client).context
        self.assertEqual(
            (context["has_selected_series"], context["start_available"], context["can_start_round"],
             context["is_first_round"], context["series_run_started"]),
            (False, False, False, True, False),
        )

    def test_can_start_round_requires_players_and_everyone_ready(self):
        """start_available - блок старта виден, can_start_round - кнопку можно нажать (как в room_start)."""
        self.room.current_series = make_series(self.host)
        self.room.save()
        self.assertEqual(self.start_flags(), {
            "players_count": 0, "ready_count": 0, "start_available": True, "can_start_round": False,
        })

        self.add_player(self.guest, is_ready=True)
        host_player = self.add_player(self.host)
        self.assertEqual(self.start_flags(), {
            "players_count": 2, "ready_count": 1, "start_available": True, "can_start_round": False,
        })

        host_player.is_ready = True
        host_player.save()
        self.assertEqual(self.start_flags(), {
            "players_count": 2, "ready_count": 2, "start_available": True, "can_start_round": True,
        })

    def test_start_is_unavailable_while_round_is_running(self):
        series = make_series(self.host)
        run = self.make_run(series)
        self.add_player(self.guest, is_ready=True)
        context = self.detail(self.client).context
        self.assertEqual((context["is_first_round"], context["series_run_started"], context["can_start_round"]),
                         (False, True, True))

        session = GameSession.objects.create(
            quiz=series.rounds.get(), mode="multiplayer", created_by=self.host, room=self.room, series_run=run,
        )
        self.room.current_game_session = session
        self.room.save()
        flags = self.start_flags()
        self.assertEqual((flags["start_available"], flags["can_start_round"]), (False, False))

    def test_players_are_listed_in_join_order(self):
        # порядок задаёт RoomPlayer.Meta.ordering по joined_at, а не pk
        first_created = self.add_player(self.guest)
        second_created = self.add_player(self.host)
        RoomPlayer.objects.filter(pk=first_created.pk).update(joined_at=second_created.joined_at + timedelta(minutes=1))
        room = self.detail(self.client).context["object"]
        self.assertEqual([p.user for p in room.room_players.all()], [self.host, self.guest])

    def test_query_count_does_not_grow_with_players_and_history(self):
        run = self.make_run(make_series(self.host, rounds=4))
        self.grow_lobby(run, 0)
        url = room_url("room_detail", self.room)
        with CaptureQueriesContext(connection) as few:
            self.client.get(url)

        self.grow_lobby(run, 1)
        self.grow_lobby(run, 2)
        with CaptureQueriesContext(connection) as many:
            self.client.get(url)
        self.assertEqual(len(many), len(few))

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
    def join(self, room):
        return self.guest_client.post(room_url("room_join", room))

    def test_join_creates_room_player(self):
        response = self.join(self.room)
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        player = RoomPlayer.objects.get()
        self.assertEqual((player.room, player.user, player.is_ready), (self.room, self.guest, False))

    def test_join_twice_keeps_single_player(self):
        self.join(self.room)
        self.join(self.room)
        self.assertEqual(RoomPlayer.objects.count(), 1)

    def test_rejoining_same_room_says_already_in_this_room(self):
        """Своя комната не считается "другой активной" - сообщение про эту комнату."""
        self.add_player(self.guest)
        response = self.join(self.room)
        self.assertEqual([m.message for m in get_messages(response.wsgi_request)], ["Вы уже участвуете в этой комнате"])

    def test_cannot_join_second_active_room(self):
        self.add_player(self.guest)
        other = Room.objects.create(title="Другая", token="other1", host=self.host)
        self.join(other)
        self.assertFalse(other.room_players.exists())

    def test_can_join_after_previous_room_finished(self):
        self.add_player(self.guest)
        Room.objects.filter(pk=self.room.pk).update(status="finished")
        other = Room.objects.create(title="Другая", token="other1", host=self.host)
        self.join(other)
        self.assertTrue(other.room_players.filter(user=self.guest).exists())

    def test_cannot_join_finished_room(self):
        Room.objects.filter(pk=self.room.pk).update(status="finished")
        self.join(self.room)
        self.assertFalse(RoomPlayer.objects.exists())

    def test_join_unknown_room_is_404(self):
        response = self.guest_client.post(reverse("multiplayer:room_join", kwargs={"code": "nosuchroom"}))
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

        self.select(self.own)
        self.make_run(self.own)
        self.assertEqual(self.guest_client.get(room_url("room_reset_quiz", self.room)).status_code, 403)
        self.assertEqual(self.guest_client.post(room_url("room_reset_quiz", self.room)).status_code, 403)
        self.assertEqual(self.current_series(), self.own)

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

    def test_reset_before_first_round_clears_series(self):
        """Серия выбрана, прогона ещё нет (current_series_run is None)."""
        self.select(self.own)
        response = self.client.post(room_url("room_reset_quiz", self.room))
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        self.assertIsNone(self.current_series())

    def test_reset_get_with_started_series_shows_confirmation(self):
        """Серия начата - GET только спрашивает: прогон не прерывается, серия остаётся."""
        run = self.make_run(make_series(self.host, rounds=3, title="Начатая"))
        GameSession.objects.create(
            quiz=run.series.rounds.get(round_order=0), mode="multiplayer", created_by=self.host,
            room=self.room, series_run=run, status="completed",
        )
        response = self.client.get(room_url("room_reset_quiz", self.room))
        self.assertTemplateUsed(response, "multiplayer/room_reset_confirm.html")
        self.assertEqual((response.context["completed_rounds"], response.context["rounds_in_series"]), (1, 3))
        run.refresh_from_db()
        self.assertEqual((self.current_series(), run.status), (run.series, "in_progress"))

    def test_reset_get_without_run_redirects_to_lobby(self):
        """Прогона нет - подтверждать нечего, GET ничего не меняет."""
        self.select(self.own)
        response = self.client.get(room_url("room_reset_quiz", self.room))
        self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
        self.assertEqual(self.current_series(), self.own)

    def test_cannot_reset_while_round_is_running(self):
        run = self.make_run(self.own)
        session = GameSession.objects.create(
            quiz=self.own.rounds.get(), mode="multiplayer", created_by=self.host, room=self.room, series_run=run,
        )
        self.room.current_game_session = session
        self.room.save()
        for method in (self.client.get, self.client.post):
            with self.subTest(method=method.__name__):
                response = method(room_url("room_reset_quiz", self.room))
                self.assertRedirects(response, room_url("room_detail", self.room), fetch_redirect_response=False)
                self.assertEqual({m.level_tag for m in get_messages(response.wsgi_request)}, {"error"})
        run.refresh_from_db()
        self.assertEqual((self.current_series(), run.status), (self.own, "in_progress"))

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

    def test_host_moderator_goes_to_game_as_observer(self):
        """Хост без RoomPlayer не участник раунда, но ведёт его со страницы игры."""
        self.add_player(self.guest, is_ready=True)
        response = self.start()
        session = GameSession.objects.get()
        self.assertRedirects(
            response, reverse("gameplay:play", kwargs={"pk": session.pk}), fetch_redirect_response=False,
        )
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


class RoomConsumerQueryCountTests(RoomTestCase):
    """room_update рендерит лобби на каждое событие для каждого подключённого - без N+1."""

    def render_update(self) -> int:
        consumer = RoomConsumer()
        consumer.scope = {"user": self.host}
        consumer.room_code = self.room.token
        sent = []
        consumer.send = lambda text_data=None, **kwargs: sent.append(text_data)
        with CaptureQueriesContext(connection) as queries:
            consumer.room_update({"type": "room.update"})
        self.assertEqual(len(sent), 1)
        return len(queries)

    def test_query_count_does_not_grow_with_players_and_history(self):
        run = self.make_run(make_series(self.host, rounds=4))
        self.grow_lobby(run, 0)
        few = self.render_update()

        self.grow_lobby(run, 1)
        self.grow_lobby(run, 2)
        self.assertEqual(self.render_update(), few)


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
        self.assertCountEqual(message, [
            "html", "can_confirm", "is_ready", "players_count", "ready_count", "start_available",
            "can_start_round", "has_selected_series", "is_first_round",
        ])
        # серия выбрана, единственный игрок ещё не подтвердил готовность
        self.assertEqual(
            (message["can_confirm"], message["is_ready"], message["has_selected_series"], message["is_first_round"]),
            (True, False, True, True),
        )
        self.assertEqual(
            (message["players_count"], message["ready_count"], message["start_available"], message["can_start_round"]),
            (1, 0, True, False),
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

    async def test_started_round_redirects_host_moderator_into_game(self):
        """Хост без RoomPlayer тоже уходит в игру - вести раунд как наблюдатель."""
        communicator, _ = await self.connect(self.host)
        session = await self.start_round()
        await self.send_update()
        self.assertEqual(
            await communicator.receive_json_from(),
            {"type": "redirect", "url": reverse("gameplay:play", kwargs={"pk": session.pk})},
        )
        await communicator.disconnect()
