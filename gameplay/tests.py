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
from django.utils import timezone

from multiplayer.models import Room, RoomPlayer
from quiz_project.testing import (
    TEST_SETTINGS, BaseTestCase, correct_option, make_round, make_series, make_user, wrong_option,
)

from quizzes.models import Question, Quiz

from .consumers import GameSessionConsumer
from .models import GameAnswer, GameParticipant, GameSession, SeriesRun
from .services import get_series_progress
from .views import _advance_series_run


def play_url(session) -> str:
    return reverse("gameplay:play", kwargs={"pk": session.pk})


def result_url(session) -> str:
    return reverse("gameplay:result", kwargs={"pk": session.pk})


def next_url(session) -> str:
    return reverse("gameplay:next_question", kwargs={"pk": session.pk})


def answer_current_question(client, session, pick=correct_option):
    """Открывает текущий вопрос (GET создаёт GameAnswer) и отвечает на него.
    pick=None - отправить форму без выбранного варианта (пропуск)."""
    response = client.get(play_url(session))
    data = {"current_answer_id": response.context["current_answer"].pk}
    if pick is not None:
        data["chosen_option_id"] = pick(response.context["current_question"]).pk
    return client.post(play_url(session), data)


def advance(client, session):
    """Кнопка "Следующий вопрос" с id вопроса, который сейчас текущий."""
    session.refresh_from_db(fields=["current_question"])
    return client.post(next_url(session), {"question_id": session.current_question_id})


class SoloTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("player")
        self.client.force_login(self.user)

    def start_run(self, series) -> SeriesRun:
        self.client.post(reverse("gameplay:start", kwargs={"pk": series.pk}))
        return SeriesRun.objects.get(series=series, created_by=self.user, status="in_progress")

    def start_round(self, run) -> GameSession:
        self.client.post(reverse("gameplay:solo_room", kwargs={"pk": run.pk}))
        return GameSession.objects.get(series_run=run, status="in_progress")

    def answer_and_advance(self, session, pick=correct_option):
        answer_current_question(self.client, session, pick)
        return advance(self.client, session)


class StartViewTests(SoloTestCase):
    def test_login_required(self):
        series = make_series(self.user)
        self.client.logout()
        self.assertLoginRequired(self.client.get(reverse("gameplay:start", kwargs={"pk": series.pk})))

    def test_get_renders_start_page_for_foreign_public_series(self):
        series = make_series(make_user("author"), status="public", rounds=3)
        response = self.client.get(reverse("gameplay:start", kwargs={"pk": series.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["rounds_count"], 3)

    def test_foreign_private_series_cannot_be_started(self):
        series = make_series(make_user("author"), status="private")
        url = reverse("gameplay:start", kwargs={"pk": series.pk})
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertFalse(SeriesRun.objects.exists())

    def test_post_creates_solo_run_and_redirects_to_solo_room(self):
        series = make_series(self.user, rounds=2)
        response = self.client.post(reverse("gameplay:start", kwargs={"pk": series.pk}))
        run = SeriesRun.objects.get()
        self.assertRedirects(
            response, reverse("gameplay:solo_room", kwargs={"pk": run.pk}), fetch_redirect_response=False,
        )
        self.assertEqual((run.mode, run.created_by, run.status, run.current_round_index, run.room),
                         ("solo", self.user, "in_progress", 0, None))

    def test_first_round_index_is_taken_from_real_round_order(self):
        """После удаления первого раунда серия начинается не с round_order=0."""
        series = make_series(self.user, rounds=0)
        make_round(series, round_order=3)
        make_round(series, round_order=5)
        run = self.start_run(series)
        self.assertEqual(run.current_round_index, 3)

    def test_second_post_reuses_run_in_progress(self):
        series = make_series(self.user)
        first = self.start_run(series)
        response = self.client.post(reverse("gameplay:start", kwargs={"pk": series.pk}))
        self.assertEqual(SeriesRun.objects.count(), 1)
        self.assertRedirects(
            response, reverse("gameplay:solo_room", kwargs={"pk": first.pk}), fetch_redirect_response=False,
        )

    def test_series_without_rounds_cannot_be_started(self):
        """Все раунды удалены: вместо создания прогона - сообщение и возврат на превью."""
        series = make_series(self.user, rounds=0)
        url = reverse("gameplay:start", kwargs={"pk": series.pk})
        preview = reverse("quizzes:quizzes_preview", kwargs={"pk": series.pk})
        for method in (self.client.get, self.client.post):
            with self.subTest(method=method.__name__):
                response = method(url)
                self.assertRedirects(response, preview, fetch_redirect_response=False)
                # сообщения копятся в сессии, пока страницу с ними не открыли, -
                # поэтому проверяем уровень, а не количество
                levels = {m.level_tag for m in get_messages(response.wsgi_request)}
                self.assertEqual(levels, {"error"})
        self.assertFalse(SeriesRun.objects.exists())

    def test_get_redirects_to_run_or_round_in_progress(self):
        series = make_series(self.user)
        url = reverse("gameplay:start", kwargs={"pk": series.pk})
        run = self.start_run(series)
        self.assertRedirects(
            self.client.get(url), reverse("gameplay:solo_room", kwargs={"pk": run.pk}),
            fetch_redirect_response=False,
        )
        session = self.start_round(run)
        self.assertRedirects(self.client.get(url), play_url(session), fetch_redirect_response=False)


class SoloRoomTests(SoloTestCase):
    def setUp(self):
        super().setUp()
        self.series = make_series(self.user, rounds=2)
        self.run = self.start_run(self.series)
        self.url = reverse("gameplay:solo_room", kwargs={"pk": self.run.pk})

    def test_owner_sees_progress(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_round"], self.series.rounds.get(round_order=0))
        self.assertFalse(response.context["is_completed"])
        self.assertEqual(response.context["completed_sessions"], [])

    def test_stranger_gets_403(self):
        self.client.force_login(make_user("stranger"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertFalse(GameSession.objects.exists())

    def test_post_creates_session_with_participant_for_current_round(self):
        response = self.client.post(self.url)
        session = GameSession.objects.get()
        self.assertRedirects(response, play_url(session), fetch_redirect_response=False)
        self.assertEqual((session.quiz, session.mode, session.series_run, session.room, session.status),
                         (self.series.rounds.get(round_order=0), "solo", self.run, None, "in_progress"))
        self.assertEqual(list(session.participants.values_list("user", flat=True)), [self.user.pk])

    def test_second_post_reuses_session_in_progress(self):
        self.client.post(self.url)
        response = self.client.post(self.url)
        self.assertEqual(GameSession.objects.count(), 1)
        self.assertRedirects(response, play_url(GameSession.objects.get()), fetch_redirect_response=False)

    def test_get_redirects_to_round_in_progress(self):
        session = self.start_round(self.run)
        self.assertRedirects(self.client.get(self.url), play_url(session), fetch_redirect_response=False)

    def test_post_is_forbidden_for_finished_run(self):
        for status in ("completed", "abandoned"):
            with self.subTest(status=status):
                SeriesRun.objects.filter(pk=self.run.pk).update(status=status)
                self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertFalse(GameSession.objects.exists())

    def test_room_player_can_read_but_not_start_multiplayer_run(self):
        host = make_user("host")
        room = Room.objects.create(title="Комната", token="roomtoken", host=host)
        RoomPlayer.objects.create(room=room, user=self.user)
        run = SeriesRun.objects.create(
            series=make_series(host, status="public"), mode="multiplayer", room=room, created_by=host,
            status="completed",
        )
        url = reverse("gameplay:solo_room", kwargs={"pk": run.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        SeriesRun.objects.filter(pk=run.pk).update(status="in_progress")
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_post_when_current_round_was_deleted_redirects_to_preview(self):
        """Раунд удалили уже после старта прогона: сессия не создаётся, прогон прерывается."""
        self.series.rounds.get(round_order=self.run.current_round_index).delete()
        response = self.client.post(self.url)
        self.assertRedirects(
            response, reverse("quizzes:quizzes_preview", kwargs={"pk": self.series.pk}),
            fetch_redirect_response=False,
        )
        self.assertEqual([m.level_tag for m in get_messages(response.wsgi_request)], ["error"])
        self.assertFalse(GameSession.objects.exists())
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "abandoned")
        self.assertIsNotNone(self.run.finished_at)

        # прерванный прогон не держит пользователя: следующий старт создаёт новый с оставшегося раунда
        new_run = self.start_run(self.series)
        self.assertNotEqual(new_run.pk, self.run.pk)
        self.assertEqual(new_run.current_round_index, 1)


class SoloFlowTests(SoloTestCase):
    def test_full_series_of_two_rounds(self):
        series = make_series(self.user, rounds=2, questions=2)
        run = self.start_run(series)

        # --- раунд 1: один верный ответ, один неверный
        first = self.start_round(run)
        q1, q2 = first.quiz.questions.order_by("order")
        # соло-старт сразу ставит указатель на первый вопрос
        self.assertEqual(first.current_question, q1)
        self.assertRedirects(answer_current_question(self.client, first), play_url(first),
                             fetch_redirect_response=False)
        # после ответа - экран вердикта на том же вопросе, указатель не сдвинут
        response = self.client.get(play_url(first))
        self.assertEqual(response.context["current_question"], q1)
        self.assertTrue(response.context["already_answered"])
        self.assertTrue(response.context["can_advance"])
        # вопрос переключает только "Следующий вопрос"
        self.assertRedirects(advance(self.client, first), play_url(first), fetch_redirect_response=False)
        first.refresh_from_db()
        self.assertEqual(first.current_question, q2)

        answer_current_question(self.client, first, pick=wrong_option)
        # на последнем вопросе "Следующий" завершает раунд
        self.assertRedirects(advance(self.client, first), result_url(first), fetch_redirect_response=False)

        first.refresh_from_db()
        participant = first.participants.get()
        self.assertEqual(first.status, "completed")
        self.assertEqual(participant.score, 1)
        self.assertIsNotNone(participant.finished_at)
        run.refresh_from_db()
        self.assertEqual((run.status, run.current_round_index, run.finished_at), ("in_progress", 1, None))

        response = self.client.get(reverse("gameplay:solo_room", kwargs={"pk": run.pk}))
        self.assertEqual(response.context["completed_sessions"], [first])
        self.assertEqual(response.context["current_round"], series.rounds.get(round_order=1))

        # --- раунд 2: оба ответа верные
        second = self.start_round(run)
        self.assertEqual(second.quiz, series.rounds.get(round_order=1))
        self.answer_and_advance(second)
        self.assertRedirects(self.answer_and_advance(second), result_url(second), fetch_redirect_response=False)

        run.refresh_from_db()
        self.assertEqual((run.status, run.current_round_index), ("completed", None))
        self.assertIsNotNone(run.finished_at)

        response = self.client.get(reverse("gameplay:solo_room", kwargs={"pk": run.pk}))
        self.assertTrue(response.context["is_completed"])
        self.assertEqual(response.context["leaderboard"], [(self.user, 3)])

        # завершённую серию можно начать заново - это новый прогон
        new_run = self.start_run(series)
        self.assertNotEqual(new_run.pk, run.pk)

    def test_completed_session_redirects_from_play_to_result(self):
        run = self.start_run(make_series(self.user, rounds=1, questions=1))
        session = self.start_round(run)
        self.answer_and_advance(session)
        self.assertRedirects(self.client.get(play_url(session)), result_url(session), fetch_redirect_response=False)

    def test_solo_result_page_renders_without_room(self):
        """GameSession.room для соло всегда None - result() не должен на этом падать."""
        run = self.start_run(make_series(self.user, rounds=1, questions=1))
        session = self.start_round(run)
        # завершение соло-раунда не трогает комнату (_notify_room только при session.room)
        self.answer_and_advance(session)
        response = self.client.get(result_url(session))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["curr_participant"].score, 1)

    def test_stranger_cannot_play_or_see_result(self):
        run = self.start_run(make_series(self.user, status="public"))
        session = self.start_round(run)
        self.client.force_login(make_user("stranger"))
        self.assertEqual(self.client.get(play_url(session)).status_code, 403)
        self.assertEqual(self.client.get(result_url(session)).status_code, 403)

    def test_play_and_result_require_login(self):
        run = self.start_run(make_series(self.user))
        session = self.start_round(run)
        self.client.logout()
        self.assertLoginRequired(self.client.get(play_url(session)))
        self.assertLoginRequired(self.client.get(result_url(session)))


class AnswerRulesTests(SoloTestCase):
    """Правила приёма ответа в play(): один ответ на вопрос, свой вариант, серверный таймер."""

    def setUp(self):
        super().setUp()
        self.series = make_series(self.user, rounds=1, questions=2)
        self.session = self.start_round(self.start_run(self.series))
        response = self.client.get(play_url(self.session))
        self.question = response.context["current_question"]
        self.answer = response.context["current_answer"]
        self.participant = self.session.participants.get()

    def post(self, option=None, answer=None, client=None):
        data = {"current_answer_id": (answer or self.answer).pk}
        if option is not None:
            data["chosen_option_id"] = option.pk
        return (client or self.client).post(play_url(self.session), data)

    def test_opening_question_creates_answer_row_once(self):
        self.client.get(play_url(self.session))
        self.assertEqual(GameAnswer.objects.count(), 1)
        self.assertIsNone(self.answer.answered_at)

    def test_correct_answer_increments_score(self):
        self.post(correct_option(self.question))
        self.answer.refresh_from_db()
        self.participant.refresh_from_db()
        self.assertTrue(self.answer.is_correct)
        self.assertFalse(self.answer.is_skipped)
        self.assertIsNotNone(self.answer.answered_at)
        self.assertEqual(self.participant.score, 1)

    def test_correct_answer_adds_points_of_the_round(self):
        Quiz.objects.filter(pk=self.session.quiz_id).update(points_per_correct=5)
        self.post(correct_option(self.question))
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.score, 5)
        # второй верный ответ прибавляет ещё столько же, а не заменяет счёт
        advance(self.client, self.session)
        response = self.client.get(play_url(self.session))
        self.post(correct_option(response.context["current_question"]), answer=response.context["current_answer"])
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.score, 10)

    def test_wrong_answer_in_expensive_round_gives_nothing(self):
        Quiz.objects.filter(pk=self.session.quiz_id).update(points_per_correct=5)
        self.post(wrong_option(self.question))
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.score, 0)

    def test_wrong_answer_does_not_increment_score(self):
        option = wrong_option(self.question)
        self.post(option)
        self.answer.refresh_from_db()
        self.participant.refresh_from_db()
        self.assertEqual((self.answer.is_correct, self.answer.is_skipped, self.answer.chosen_option),
                         (False, False, option))
        self.assertEqual(self.participant.score, 0)

    def test_post_without_option_is_a_skip(self):
        self.post()
        self.answer.refresh_from_db()
        self.assertTrue(self.answer.is_skipped)
        self.assertFalse(self.answer.is_correct)
        self.assertIsNone(self.answer.chosen_option)
        # пропуск - тоже ответ: экран вердикта на том же вопросе, можно переходить дальше
        response = self.client.get(play_url(self.session))
        self.assertEqual(response.context["current_question"], self.question)
        self.assertTrue(response.context["already_answered"])
        self.assertTrue(response.context["can_advance"])

    def test_answer_after_time_limit_is_skipped_even_if_correct(self):
        GameAnswer.objects.filter(pk=self.answer.pk).update(
            shown_at=timezone.now() - timedelta(seconds=self.session.quiz.time_limit_seconds + 5),
        )
        self.post(correct_option(self.question))
        self.answer.refresh_from_db()
        self.participant.refresh_from_db()
        self.assertTrue(self.answer.is_skipped)
        self.assertFalse(self.answer.is_correct)
        self.assertEqual(self.participant.score, 0)

    def test_remaining_seconds_never_negative(self):
        GameAnswer.objects.filter(pk=self.answer.pk).update(shown_at=timezone.now() - timedelta(hours=1))
        response = self.client.get(play_url(self.session))
        self.assertEqual(response.context["remaining_seconds"], 0)

    def test_question_cannot_be_answered_twice(self):
        self.post(wrong_option(self.question))
        response = self.post(correct_option(self.question))
        self.assertEqual(response.status_code, 403)
        self.answer.refresh_from_db()
        self.participant.refresh_from_db()
        self.assertFalse(self.answer.is_correct)
        self.assertEqual(self.participant.score, 0)

    def test_option_of_another_question_is_rejected(self):
        other_question = self.session.quiz.questions.exclude(pk=self.question.pk).get()
        response = self.post(correct_option(other_question))
        self.assertEqual(response.status_code, 403)
        self.answer.refresh_from_db()
        self.assertIsNone(self.answer.answered_at)

    def test_stranger_cannot_answer_for_participant(self):
        stranger = Client()
        stranger.force_login(make_user("stranger"))
        response = self.post(correct_option(self.question), client=stranger)
        self.assertEqual(response.status_code, 404)
        self.answer.refresh_from_db()
        self.assertIsNone(self.answer.answered_at)

    def test_answer_of_another_session_is_rejected(self):
        other_series = make_series(self.user, rounds=1, title="Другой")
        other_session = self.start_round(self.start_run(other_series))
        other_answer = self.client.get(play_url(other_session)).context["current_answer"]
        response = self.post(answer=other_answer)
        self.assertEqual(response.status_code, 404)


class SoloNextQuestionTests(SoloTestCase):
    """next_question в соло: переключает только сам игрок и только после своего ответа."""

    def setUp(self):
        super().setUp()
        self.session = self.start_round(self.start_run(make_series(self.user, rounds=1, questions=2)))
        self.first, self.second = self.session.quiz.questions.order_by("order")

    def pointer(self):
        self.session.refresh_from_db()
        return self.session.current_question

    def test_login_required_and_post_only(self):
        url = next_url(self.session)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertLoginRequired(self.client.post(url, {"question_id": self.first.pk}))

    def test_unknown_session_is_404(self):
        url = reverse("gameplay:next_question", kwargs={"pk": 999_999})
        self.assertEqual(self.client.post(url, {"question_id": self.first.pk}).status_code, 404)

    def test_question_without_answer_cannot_be_skipped(self):
        # ни строки ответа, ни открытого, но не отвеченного вопроса
        self.assertEqual(advance(self.client, self.session).status_code, 403)
        self.client.get(play_url(self.session))
        self.assertEqual(advance(self.client, self.session).status_code, 403)
        self.assertEqual(self.pointer(), self.first)

    def test_stranger_gets_403(self):
        """У соло-сессии нет комнаты - проверка хоста не должна падать на session.room."""
        answer_current_question(self.client, self.session)
        stranger = Client()
        stranger.force_login(make_user("stranger"))
        self.assertEqual(advance(stranger, self.session).status_code, 403)
        self.assertEqual(self.pointer(), self.first)

    def test_double_click_moves_pointer_once(self):
        answer_current_question(self.client, self.session)
        data = {"question_id": self.first.pk}
        self.client.post(next_url(self.session), data)
        response = self.client.post(next_url(self.session), data)
        self.assertRedirects(response, play_url(self.session), fetch_redirect_response=False)
        self.assertEqual(self.pointer(), self.second)

    def test_last_question_completes_round_and_advances_run(self):
        self.answer_and_advance(self.session)
        answer_current_question(self.client, self.session)
        response = advance(self.client, self.session)
        self.assertRedirects(response, result_url(self.session), fetch_redirect_response=False)
        self.session.refresh_from_db()
        self.assertEqual((self.session.status, self.session.current_question), ("completed", None))
        self.assertIsNotNone(self.session.participants.get().finished_at)
        self.session.series_run.refresh_from_db()
        self.assertEqual(self.session.series_run.status, "completed")

    def test_completed_session_redirects_to_result(self):
        self.answer_and_advance(self.session)
        self.answer_and_advance(self.session)
        response = self.client.post(next_url(self.session), {"question_id": self.second.pk})
        self.assertRedirects(response, result_url(self.session), fetch_redirect_response=False)


class PlayContextTests(SoloTestCase):
    """Контекст экрана игры: номер вопроса, прогресс по точкам, серия, очки."""

    def setUp(self):
        super().setUp()
        self.session = self.start_round(self.start_run(make_series(self.user, rounds=1, questions=4)))
        self.questions = list(self.session.quiz.questions.order_by("order"))

    def context(self):
        return self.client.get(play_url(self.session)).context

    def states(self, ctx):
        return [item["state"] for item in ctx["answers_progress"]]

    def test_question_number_ignores_gaps_in_order(self):
        for question, order in zip(self.questions, (0, 5, 6, 20)):
            Question.objects.filter(pk=question.pk).update(order=order)
        for number, is_last in ((1, False), (2, False), (3, False), (4, True)):
            with self.subTest(number=number):
                ctx = self.context()
                self.assertEqual((ctx["question_number"], ctx["question_count"], ctx["is_last_question"]),
                                 (number, 4, is_last))
                self.answer_and_advance(self.session)

    def test_progress_streak_and_points(self):
        Quiz.objects.filter(pk=self.session.quiz_id).update(points_per_correct=5)
        ctx = self.context()
        self.assertEqual(self.states(ctx), ["current", "pending", "pending", "pending"])
        self.assertEqual((ctx["streak"], ctx["score"], ctx["can_advance"]), (0, 0, False))

        self.answer_and_advance(self.session)
        self.answer_and_advance(self.session, pick=wrong_option)
        self.answer_and_advance(self.session)
        # четвёртый вопрос открыт, ответа ещё нет: серия - только третий
        ctx = self.context()
        self.assertEqual(self.states(ctx), ["correct", "wrong", "correct", "current"])
        self.assertEqual(
            (ctx["streak"], ctx["points_gained"], ctx["score"], ctx["already_answered"], ctx["can_advance"]),
            (1, 0, 10, False, False),
        )

        # ответил верно - на экране вердикта точка окрашена, серия выросла, очки этого вопроса - 5
        answer_current_question(self.client, self.session)
        ctx = self.context()
        self.assertEqual(self.states(ctx), ["correct", "wrong", "correct", "correct"])
        self.assertEqual(
            (ctx["streak"], ctx["points_gained"], ctx["score"], ctx["already_answered"], ctx["can_advance"]),
            (2, 5, 15, True, True),
        )

    def test_player_never_sees_which_option_is_correct(self):
        """Признак верного варианта есть только у ведущего - ни до ответа, ни после неверного."""
        for step in ("question", "verdict"):
            with self.subTest(step=step):
                if step == "verdict":
                    answer_current_question(self.client, self.session, pick=wrong_option)
                response = self.client.get(play_url(self.session))
                self.assertNotContains(response, "is-correct")
                self.assertNotContains(response, "Верный ответ")

    def test_skip_breaks_streak(self):
        self.answer_and_advance(self.session)
        self.answer_and_advance(self.session, pick=None)
        ctx = self.context()
        self.assertEqual(self.states(ctx)[:3], ["correct", "skipped", "current"])
        self.assertEqual(ctx["streak"], 0)


class AdvanceSeriesRunTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("player")

    def test_next_round_is_found_by_value_not_by_position(self):
        """Дыра в round_order (раунд удалён из середины) не должна ломать продвижение."""
        series = make_series(self.user, rounds=0)
        make_round(series, round_order=0)
        make_round(series, round_order=2)
        make_round(series, round_order=7)
        run = SeriesRun.objects.create(series=series, mode="solo", created_by=self.user, current_round_index=0)

        _advance_series_run(run, completed_round_order=0)
        self.assertEqual((run.current_round_index, run.status), (2, "in_progress"))
        _advance_series_run(run, completed_round_order=2)
        self.assertEqual((run.current_round_index, run.status), (7, "in_progress"))

    def test_last_round_completes_run(self):
        series = make_series(self.user, rounds=1)
        run = SeriesRun.objects.create(series=series, mode="solo", created_by=self.user, current_round_index=0)
        _advance_series_run(run, completed_round_order=0)
        run.refresh_from_db()
        self.assertEqual((run.status, run.current_round_index), ("completed", None))
        self.assertIsNotNone(run.finished_at)


class SeriesProgressTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.series = make_series(self.alice, rounds=2)
        self.run = SeriesRun.objects.create(
            series=self.series, mode="multiplayer", created_by=self.alice, current_round_index=1,
        )

    def make_session(self, round_order, status, scores):
        session = GameSession.objects.create(
            quiz=self.series.rounds.get(round_order=round_order), mode="multiplayer",
            created_by=self.alice, series_run=self.run, status=status,
        )
        for user, score in scores.items():
            GameParticipant.objects.create(session=session, user=user, score=score)
        return session

    def test_leaderboard_sums_only_completed_rounds_and_sorts_desc(self):
        completed = self.make_session(0, "completed", {self.alice: 1, self.bob: 2})
        self.make_session(1, "in_progress", {self.alice: 5, self.bob: 0})
        progress = get_series_progress(self.run)
        self.assertEqual(progress["leaderboard"], [(self.bob, 2), (self.alice, 1)])
        self.assertEqual(progress["completed_sessions"], [completed])
        self.assertEqual(progress["current_round"], self.series.rounds.get(round_order=1))
        self.assertTrue(progress["is_current_round_is_in_progress"])
        self.assertFalse(progress["is_completed"])

    def test_current_round_not_in_progress_without_active_session(self):
        self.make_session(0, "completed", {self.alice: 1})
        self.assertFalse(get_series_progress(self.run)["is_current_round_is_in_progress"])

    def test_rounds_progress_follows_round_order_with_gaps(self):
        # раунды созданы не по порядку и с дырками в round_order (как после RoundDeleteView)
        make_round(self.series, round_order=5)
        make_round(self.series, round_order=3)
        self.run.current_round_index = 3
        first = self.make_session(0, "completed", {self.alice: 1})
        second = self.make_session(1, "completed", {self.alice: 1})
        self.make_session(3, "in_progress", {self.alice: 0})

        progress = get_series_progress(self.run)
        self.assertEqual(
            [(item["round"].round_order, item["state"], item["session"]) for item in progress["rounds_progress"]],
            [(0, "completed", first), (1, "completed", second), (3, "current", None), (5, "pending", None)],
        )
        self.assertEqual(progress["rounds_count"], 4)

    def test_abandoned_run_counts_as_completed(self):
        for status, expected in (("in_progress", False), ("completed", True), ("abandoned", True)):
            with self.subTest(status=status):
                self.run.status = status
                self.assertEqual(get_series_progress(self.run)["is_completed"], expected)


class MultiplayerTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.host = make_user("host")
        self.guest = make_user("guest")
        self.series = make_series(self.host, rounds=2, questions=2)
        self.room = Room.objects.create(title="Комната", token="roomtoken", host=self.host,
                                        current_series=self.series)
        self.host_client = Client()
        self.host_client.force_login(self.host)
        self.guest_client = Client()
        self.guest_client.force_login(self.guest)
        self.start_url = reverse("multiplayer:room_start", kwargs={"code": self.room.token})

    def join(self, *users):
        for user in users:
            RoomPlayer.objects.create(room=self.room, user=user, is_ready=True)

    def start_round(self) -> GameSession:
        self.room.room_players.update(is_ready=True)
        self.host_client.post(self.start_url)
        return GameSession.objects.get(room=self.room, status="in_progress")

    def pointer(self, session):
        session.refresh_from_db()
        return session.current_question


class MultiplayerFlowTests(MultiplayerTestCase):
    """Сквозной мультиплеер: room_start -> общий текущий вопрос -> завершение раунда -> следующий раунд."""

    def test_answers_do_not_move_question_only_host_does(self):
        self.join(self.host, self.guest)
        session = self.start_round()
        first, second = session.quiz.questions.order_by("order")
        self.assertEqual(session.current_question, first)

        answer_current_question(self.host_client, session)
        response = self.host_client.get(play_url(session))
        self.assertEqual(response.context["mode"], "multiplayer")
        self.assertTrue(response.context["already_answered"])
        self.assertFalse(self.guest_client.get(play_url(session)).context["already_answered"])

        # ответили все - автопродвижения нет, вопрос стоит до кнопки хоста
        answer_current_question(self.guest_client, session, pick=wrong_option)
        self.assertEqual(self.pointer(session), first)
        self.assertTrue(self.host_client.get(play_url(session)).context["all_answered"])

        self.assertRedirects(advance(self.host_client, session), play_url(session), fetch_redirect_response=False)
        self.assertEqual(self.pointer(session), second)

    def test_answer_stats_count_skips(self):
        self.join(self.host, self.guest)
        session = self.start_round()
        answer_current_question(self.host_client, session, pick=None)
        ctx = self.host_client.get(play_url(session)).context
        self.assertEqual((ctx["answered_count"], ctx["participants_count"], ctx["all_answered"]), (1, 2, False))
        # хост-игрок ответил - может переключать; гость - не может, даже ответив
        self.assertTrue(ctx["can_advance"])
        answer_current_question(self.guest_client, session)
        ctx = self.guest_client.get(play_url(session)).context
        self.assertFalse(ctx["can_advance"])
        self.assertEqual((ctx["answered_count"], ctx["all_answered"]), (2, True))

    def play_round(self, session):
        for _ in range(session.quiz.questions.count()):
            answer_current_question(self.host_client, session)
            answer_current_question(self.guest_client, session, pick=wrong_option)
            advance(self.host_client, session)

    def test_full_series_of_two_rounds(self):
        self.join(self.host, self.guest)

        # --- раунд 1
        first = self.start_round()
        self.room.refresh_from_db()
        run = self.room.current_series_run
        self.assertEqual((self.room.status, self.room.current_game_session), ("in_progress", first))
        self.assertEqual((run.mode, run.room, run.created_by, run.current_round_index),
                         ("multiplayer", self.room, self.host, 0))

        self.play_round(first)
        # "К результатам" на последнем вопросе сразу завершает раунд
        first.refresh_from_db()
        self.assertEqual((first.status, first.current_question), ("completed", None))
        self.assertRedirects(self.host_client.get(play_url(first)), result_url(first),
                             fetch_redirect_response=False)
        self.assertRedirects(self.guest_client.get(play_url(first)), result_url(first),
                             fetch_redirect_response=False)

        first.refresh_from_db()
        self.assertEqual(first.status, "completed")
        self.assertTrue(all(p.finished_at for p in first.participants.all()))
        # комната вернулась в лобби, готовность сброшена, серия и прогон остались
        self.room.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual((self.room.status, self.room.current_game_session), ("waiting", None))
        self.assertEqual((self.room.current_series, self.room.current_series_run), (self.series, run))
        self.assertFalse(self.room.room_players.filter(is_ready=True).exists())
        self.assertEqual((run.status, run.current_round_index), ("in_progress", 1))

        # --- раунд 2 идёт в том же прогоне серии
        second = self.start_round()
        self.assertEqual(second.quiz, self.series.rounds.get(round_order=1))
        self.assertEqual(second.series_run, run)
        self.assertEqual(SeriesRun.objects.count(), 1)
        self.play_round(second)

        run.refresh_from_db()
        self.assertEqual(run.status, "completed")
        self.assertIsNotNone(run.finished_at)
        # после последнего раунда комната полностью сбрасывается
        self.room.refresh_from_db()
        self.assertEqual(
            (self.room.status, self.room.current_series, self.room.current_series_run, self.room.current_game_session),
            ("waiting", None, None, None),
        )
        self.assertEqual(get_series_progress(run)["leaderboard"], [(self.host, 4), (self.guest, 0)])

        # результаты видны обоим игрокам, лобби открывается без ошибок
        for client in (self.host_client, self.guest_client):
            self.assertEqual(client.get(result_url(second)).status_code, 200)
            detail = client.get(reverse("multiplayer:room_detail", kwargs={"code": self.room.token}))
            self.assertEqual(detail.status_code, 200)

    def test_host_moderator_watches_game_without_answer_rows(self):
        """Хост, не являющийся RoomPlayer: ведёт игру как наблюдатель, сам не отвечает."""
        self.join(self.guest)
        response = self.host_client.post(self.start_url)
        session = GameSession.objects.get()
        self.assertRedirects(response, play_url(session), fetch_redirect_response=False)
        self.assertEqual(list(session.participants.values_list("user", flat=True)), [self.guest.pk])

        response = self.host_client.get(play_url(session))
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertEqual((ctx["role"], ctx["current_answer"], ctx["can_advance"]), ("host_observer", None, True))
        self.assertEqual((ctx["answered_count"], ctx["participants_count"], ctx["all_answered"]), (0, 1, False))
        # наблюдателю строка ответа не создаётся - таймер у него не стартует
        self.assertFalse(GameAnswer.objects.exists())
        # верный вариант подсвечен - ровно один
        self.assertContains(response, "option-card is-static is-correct", count=1)
        self.assertContains(response, "Верный ответ", count=1)

        response = self.host_client.get(result_url(session))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["curr_participant"])

    def test_stranger_cannot_play_or_see_multiplayer_result(self):
        self.join(self.guest)
        self.host_client.post(self.start_url)
        session = GameSession.objects.get()
        stranger = Client()
        stranger.force_login(make_user("stranger"))
        self.assertEqual(stranger.get(play_url(session)).status_code, 403)
        self.assertEqual(stranger.get(result_url(session)).status_code, 403)


class MultiplayerNextQuestionTests(MultiplayerTestCase):
    """next_question в мультиплеере: только хост, пропуск неответивших, поздние ответы."""

    def test_only_host_can_advance(self):
        self.join(self.host, self.guest)
        session = self.start_round()
        first = session.current_question
        answer_current_question(self.guest_client, session)
        self.assertEqual(advance(self.guest_client, session).status_code, 403)
        self.assertEqual(self.pointer(session), first)

    def test_playing_host_must_answer_first(self):
        self.join(self.host, self.guest)
        session = self.start_round()
        first, second = session.quiz.questions.order_by("order")
        self.assertEqual(advance(self.host_client, session).status_code, 403)
        self.assertEqual(self.pointer(session), first)
        answer_current_question(self.host_client, session)
        # гость не ответил - хост всё равно может переключить ("Пропустить ожидание")
        self.assertRedirects(advance(self.host_client, session), play_url(session), fetch_redirect_response=False)
        self.assertEqual(self.pointer(session), second)

    def test_skip_marks_everyone_who_did_not_answer(self):
        opened, never_opened = make_user("opened"), make_user("never_opened")
        opened_client = Client()
        opened_client.force_login(opened)
        self.join(self.guest, opened, never_opened)
        session = self.start_round()
        first = session.current_question
        answer_current_question(self.guest_client, session)
        opened_client.get(play_url(session))  # открыл вопрос, но не ответил

        advance(self.host_client, session)

        answers = {
            a.participant.user: a
            for a in GameAnswer.objects.filter(question=first).select_related("participant__user")
        }
        # строка появилась и у того, кто вопрос так и не открыл
        self.assertEqual(set(answers), {self.guest, opened, never_opened})
        answered = answers[self.guest]
        self.assertEqual((answered.is_correct, answered.is_skipped), (True, False))
        self.assertIsNotNone(answered.answered_at)
        for user in (opened, never_opened):
            with self.subTest(user=user.username):
                skipped = answers[user]
                self.assertEqual((skipped.is_skipped, skipped.is_correct, skipped.answered_at), (True, False, None))
        scores = dict(session.participants.values_list("user__username", "score"))
        self.assertEqual(scores, {"guest": 1, "opened": 0, "never_opened": 0})

    def test_late_answer_to_switched_question_is_rejected(self):
        self.join(self.guest)
        session = self.start_round()
        response = self.guest_client.get(play_url(session))
        stale_answer, question = response.context["current_answer"], response.context["current_question"]
        advance(self.host_client, session)

        response = self.guest_client.post(play_url(session), {
            "current_answer_id": stale_answer.pk, "chosen_option_id": correct_option(question).pk,
        })
        self.assertEqual(response.status_code, 403)
        stale_answer.refresh_from_db()
        self.assertEqual((stale_answer.is_skipped, stale_answer.is_correct), (True, False))
        self.assertEqual(session.participants.get().score, 0)

    def test_last_question_completes_round_for_everyone(self):
        self.join(self.guest)
        session = self.start_round()
        advance(self.host_client, session)
        self.assertRedirects(advance(self.host_client, session), result_url(session), fetch_redirect_response=False)
        session.refresh_from_db()
        self.assertEqual(session.status, "completed")
        self.room.refresh_from_db()
        self.assertEqual((self.room.status, self.room.current_game_session), ("waiting", None))
        self.assertRedirects(self.guest_client.get(play_url(session)), result_url(session),
                             fetch_redirect_response=False)


class PlayQueryCountTests(BaseTestCase):
    """GET play не делает запросов в цикле по вопросам и участникам."""

    def make_session(self, questions, players):
        host = make_user(f"host_{questions}")
        quiz = make_series(host, rounds=1, questions=questions, title=f"Квиз {questions}").rounds.get()
        question_list = list(quiz.questions.order_by("order"))
        room = Room.objects.create(title="Комната", token=f"token{questions}", host=host)
        session = GameSession.objects.create(
            quiz=quiz, mode="multiplayer", created_by=host, room=room, current_question=question_list[-1],
        )
        users = [make_user(f"player_{questions}_{i}") for i in range(players)]
        for user in users:
            participant = GameParticipant.objects.create(session=session, user=user)
            for question in question_list[:-1]:
                GameAnswer.objects.create(participant=participant, question=question,
                                          answered_at=timezone.now(), is_correct=True)
        client = Client()
        client.force_login(users[0])
        client.get(play_url(session))  # первый заход создаёт строку ответа на текущий вопрос
        return client, session

    def count_queries(self, client, session) -> int:
        with CaptureQueriesContext(connection) as queries:
            response = client.get(play_url(session))
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_query_count_does_not_grow_with_questions_and_players(self):
        small = self.count_queries(*self.make_session(questions=2, players=1))
        big = self.count_queries(*self.make_session(questions=6, players=4))
        self.assertEqual(small, big)


@override_settings(**TEST_SETTINGS)
class GameSessionConsumerTests(TransactionTestCase):
    """WebSocket экрана игры: кого пускаем и что шлём на session.update."""

    def setUp(self):
        self.host = make_user("host")
        self.guest = make_user("guest")
        series = make_series(self.host, rounds=1, questions=2)
        self.quiz = series.rounds.get()
        self.first, self.second = self.quiz.questions.order_by("order")
        self.session = GameSession.objects.create(
            quiz=self.quiz, mode="multiplayer", created_by=self.host, current_question=self.first,
        )
        GameParticipant.objects.create(session=self.session, user=self.host)
        self.guest_participant = GameParticipant.objects.create(session=self.session, user=self.guest)

    async def connect(self, user, pk=None):
        """Подключение; снимок состояния, который консьюмер шлёт сразу после accept, - в communicator.snapshot."""
        pk = pk or self.session.pk
        communicator = WebsocketCommunicator(GameSessionConsumer.as_asgi(), f"/ws/gameplay/session/{pk}/")
        communicator.scope["user"] = user
        communicator.scope["url_route"] = {"kwargs": {"pk": pk}}
        connected, _ = await communicator.connect()
        communicator.snapshot = await communicator.receive_json_from() if connected else None
        return communicator, connected

    async def send_update(self):
        await get_channel_layer().group_send(f"session_{self.session.pk}", {"type": "session.update"})

    async def test_only_participants_can_connect(self):
        stranger = await sync_to_async(make_user)("stranger")
        cases = [
            (AnonymousUser(), None, False),
            (stranger, None, False),
            (self.host, 999_999, False),
            (self.guest, None, True),
        ]
        for user, pk, expected in cases:
            with self.subTest(user=str(user), pk=pk):
                communicator, connected = await self.connect(user, pk)
                self.assertEqual(connected, expected)
                await communicator.disconnect()

    async def test_update_without_question_change_sends_players_status(self):
        communicator, _ = await self.connect(self.host)
        await GameAnswer.objects.acreate(participant=self.guest_participant, question=self.first,
                                         answered_at=timezone.now())
        await self.send_update()
        message = await communicator.receive_json_from()
        self.assertEqual(message["type"], "status")
        self.assertIn("html", message)
        self.assertEqual(
            {key: message[key] for key in ("answered_count", "participants_count", "all_answered",
                                           "current_question_id")},
            {"answered_count": 1, "participants_count": 2, "all_answered": False,
             "current_question_id": self.first.pk},
        )
        await communicator.disconnect()

    async def test_connect_sends_current_state(self):
        """Сигнал, отправленный пока страница грузилась, теряется - поэтому при подключении приходит снимок."""
        communicator, _ = await self.connect(self.guest)
        self.assertEqual((communicator.snapshot["type"], communicator.snapshot["current_question_id"]),
                         ("status", self.first.pk))
        await communicator.disconnect()

    async def test_connect_to_completed_session_redirects_to_result(self):
        await GameSession.objects.filter(pk=self.session.pk).aupdate(status="completed", current_question=None)
        communicator, connected = await self.connect(self.guest)
        self.assertTrue(connected)
        self.assertEqual(communicator.snapshot, {"type": "redirect", "url": result_url(self.session)})
        await communicator.disconnect()

    async def attach_room(self):
        moderator = await sync_to_async(make_user)("moderator")
        room = await Room.objects.acreate(title="Комната", token="roomtoken", host=moderator)
        await GameSession.objects.filter(pk=self.session.pk).aupdate(room=room)
        return moderator

    async def test_host_observer_connects_and_gets_status(self):
        """Хост комнаты, не участник сессии: подключается и получает фрагмент со счётчиками."""
        moderator = await self.attach_room()
        communicator, connected = await self.connect(moderator)
        self.assertTrue(connected)
        await self.send_update()
        message = await communicator.receive_json_from()
        self.assertEqual((message["type"], message["participants_count"], message["answered_count"]),
                         ("status", 2, 0))
        await communicator.disconnect()

    async def test_stranger_cannot_connect_to_room_session(self):
        await self.attach_room()
        stranger = await sync_to_async(make_user)("stranger")
        communicator, connected = await self.connect(stranger)
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_question_change_sends_redirect_to_play(self):
        communicator, _ = await self.connect(self.host)
        await GameSession.objects.filter(pk=self.session.pk).aupdate(current_question=self.second)
        await self.send_update()
        self.assertEqual(
            await communicator.receive_json_from(),
            {"type": "redirect", "url": play_url(self.session)},
        )
        await communicator.disconnect()

    async def test_completed_session_sends_redirect_to_result(self):
        communicator, _ = await self.connect(self.host)
        await GameSession.objects.filter(pk=self.session.pk).aupdate(status="completed", current_question=None)
        await self.send_update()
        self.assertEqual(
            await communicator.receive_json_from(),
            {"type": "redirect", "url": result_url(self.session)},
        )
        await communicator.disconnect()
