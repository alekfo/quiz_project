from datetime import timedelta

from django.contrib.auth.models import AnonymousUser
from django.urls import reverse
from django.utils import timezone

from ai_generator.models import GenerationRequest
from quiz_project.testing import BaseTestCase, make_category, make_round, make_series, make_user
from social.models import QuizSeriesLike, SavedQuizSeries

from .models import AnswerOption, Question, Quiz, QuizSeries
from .services import create_quiz_from_any_data


def question_formset_data(questions: list[dict], initial: int = 0) -> dict:
    """POST-данные формсета вопросов (prefix 'questions' - related_name FK Question.quiz)."""
    data = {
        "questions-TOTAL_FORMS": str(len(questions)),
        "questions-INITIAL_FORMS": str(initial),
        "questions-MIN_NUM_FORMS": "0",
        "questions-MAX_NUM_FORMS": "1000",
    }
    for i, question in enumerate(questions):
        fields = {
            "text": f"Вопрос {i}",
            "order": str(i),
            "fact": "",
            "option_1": "A",
            "option_2": "B",
            "option_3": "C",
            "option_4": "D",
            "correct_index": "0",
        }
        fields.update(question)
        for name, value in fields.items():
            data[f"questions-{i}-{name}"] = value
    return data


def round_data(**kwargs) -> dict:
    data = {
        "subject": "Тема раунда",
        "level": "common",
        "style": "serious",
        "audience": "common",
        "time_limit_seconds": "30",
    }
    data.update(kwargs)
    return data


class QuizSeriesQuerySetTests(BaseTestCase):
    """Три правила доступа к серии: visible_to / saved_by_user / available_to."""

    def setUp(self):
        super().setUp()
        self.author = make_user("author")
        self.reader = make_user("reader")
        self.public = make_series(self.author, status="public", title="public")
        self.private = make_series(self.author, status="private", title="private")

    def test_visible_to_author_sees_own_private_and_public(self):
        self.assertCountEqual(QuizSeries.objects.visible_to(self.author), [self.public, self.private])

    def test_visible_to_other_user_sees_only_public(self):
        self.assertCountEqual(QuizSeries.objects.visible_to(self.reader), [self.public])

    def test_visible_to_anonymous_sees_only_public(self):
        self.assertCountEqual(QuizSeries.objects.visible_to(AnonymousUser()), [self.public])

    def test_saved_by_user_returns_saved_public_series(self):
        SavedQuizSeries.objects.create(user=self.reader, series=self.public)
        self.assertCountEqual(QuizSeries.objects.saved_by_user(self.reader), [self.public])
        self.assertCountEqual(QuizSeries.objects.saved_by_user(self.author), [])

    def test_saved_series_hidden_while_private_and_back_when_public_again(self):
        SavedQuizSeries.objects.create(user=self.reader, series=self.public)
        QuizSeries.objects.filter(pk=self.public.pk).update(status="private")
        self.assertCountEqual(QuizSeries.objects.saved_by_user(self.reader), [])
        self.assertCountEqual(QuizSeries.objects.available_to(self.reader), [])
        # строка сохранения не удаляется - серия возвращается, как только снова стала публичной
        self.assertTrue(SavedQuizSeries.objects.filter(user=self.reader, series=self.public).exists())
        QuizSeries.objects.filter(pk=self.public.pk).update(status="public")
        self.assertCountEqual(QuizSeries.objects.saved_by_user(self.reader), [self.public])

    def test_available_to_is_own_plus_saved_public(self):
        own = make_series(self.reader, status="private", title="own")
        SavedQuizSeries.objects.create(user=self.reader, series=self.public)
        self.assertCountEqual(QuizSeries.objects.available_to(self.reader), [own, self.public])

    def test_available_to_excludes_public_series_that_was_not_saved(self):
        self.assertCountEqual(QuizSeries.objects.available_to(self.reader), [])

    def test_available_to_has_no_duplicates_when_own_series_saved_by_several_users(self):
        for name in ("u1", "u2", "u3"):
            SavedQuizSeries.objects.create(user=make_user(name), series=self.public)
        available = list(QuizSeries.objects.available_to(self.author))
        self.assertEqual(len(available), 2)
        self.assertCountEqual(available, [self.public, self.private])

    def test_saved_and_available_are_empty_for_anonymous(self):
        self.assertFalse(QuizSeries.objects.saved_by_user(AnonymousUser()).exists())
        self.assertFalse(QuizSeries.objects.available_to(AnonymousUser()).exists())


class LoginRequiredTests(BaseTestCase):
    def test_pages_redirect_anonymous_to_login(self):
        series = make_series(make_user("author"), status="public")
        round_ = series.rounds.first()
        urls = [
            reverse("quizzes:quizzes_list"),
            reverse("quizzes:quizzes_create"),
            reverse("quizzes:quizzes_create_for_series", kwargs={"series_id": series.pk}),
            reverse("quizzes:quizzes_preview", kwargs={"pk": series.pk}),
            reverse("quizzes:quizzes_details", kwargs={"pk": series.pk}),
            reverse("quizzes:quiz_update_main_info", kwargs={"pk": series.pk}),
            reverse("quizzes:quiz_delete", kwargs={"pk": series.pk}),
            reverse("quizzes:round_update", kwargs={"pk": round_.pk}),
            reverse("quizzes:round_delete", kwargs={"pk": round_.pk}),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertLoginRequired(self.client.get(url))

    def test_menu_is_open_for_anonymous(self):
        self.assertEqual(self.client.get(reverse("quizzes:menu")).status_code, 200)

    def test_root_redirects_to_menu(self):
        response = self.client.get("/")
        self.assertRedirects(response, reverse("quizzes:menu"), fetch_redirect_response=False)


class MenuShowcaseTests(BaseTestCase):
    """Витрина на главной: чужие публичные серии, которые пользователь ещё не сохранил."""

    def setUp(self):
        super().setUp()
        self.author = make_user("author")
        self.reader = make_user("reader")
        self.url = reverse("quizzes:menu")

    def showcase(self, user=None) -> list:
        if user is not None:
            self.client.force_login(user)
        return list(self.client.get(self.url).context["showcase_series"])

    def test_shows_only_foreign_public_unsaved_series_with_rounds(self):
        shown = make_series(self.author, status="public", title="показывается")
        make_series(self.author, status="private", title="приватная")
        make_series(self.author, status="public", rounds=0, title="без раундов")
        make_series(self.reader, status="public", title="своя")
        saved = make_series(self.author, status="public", title="уже сохранена")
        SavedQuizSeries.objects.create(user=self.reader, series=saved)

        self.assertEqual(self.showcase(self.reader), [shown])

    def test_series_saved_by_another_user_is_still_shown(self):
        series = make_series(self.author, status="public")
        SavedQuizSeries.objects.create(user=make_user("other"), series=series)
        self.assertEqual(self.showcase(self.reader), [series])

    def test_anonymous_sees_all_public_series_with_rounds(self):
        first = make_series(self.author, status="public", title="A")
        second = make_series(self.reader, status="public", title="B")
        make_series(self.author, status="private", title="приватная")
        make_series(self.author, status="public", rounds=0, title="без раундов")
        self.assertCountEqual(self.showcase(), [first, second])

    def test_counters_are_not_multiplied_by_joins(self):
        """Два Count по разным связям без distinct=True дали бы 3*2=6 в обоих счётчиках."""
        series = make_series(self.author, status="public", rounds=3)
        for name in ("u1", "u2"):
            QuizSeriesLike.objects.create(series=series, user=make_user(name))
        [shown] = self.showcase(self.reader)
        self.assertEqual((shown.rounds_count, shown.like_count), (3, 2))

    def test_ordered_by_likes_then_newest(self):
        old = make_series(self.author, status="public", title="старая")
        liked = make_series(self.author, status="public", title="с лайком")
        new = make_series(self.author, status="public", title="новая")
        QuizSeriesLike.objects.create(series=liked, user=make_user("fan"))
        # created_at задаём явно: в тесте серии создаются почти одновременно
        now = timezone.now()
        QuizSeries.objects.filter(pk=old.pk).update(created_at=now - timedelta(days=2))
        QuizSeries.objects.filter(pk=liked.pk).update(created_at=now - timedelta(days=3))
        QuizSeries.objects.filter(pk=new.pk).update(created_at=now - timedelta(days=1))

        self.assertEqual(self.showcase(self.reader), [liked, new, old])

    def test_limited_to_twelve_series(self):
        for i in range(14):
            make_series(self.author, status="public", title=f"Квиз {i}")
        self.assertEqual(len(self.showcase(self.reader)), 12)

    def test_page_renders_cards_with_links_to_preview(self):
        series = make_series(self.author, status="public", title="Космос")
        for client_user in (None, self.reader):
            with self.subTest(user=client_user):
                if client_user:
                    self.client.force_login(client_user)
                response = self.client.get(self.url)
                self.assertContains(response, "Космос")
                self.assertContains(response, reverse("quizzes:quizzes_preview", kwargs={"pk": series.pk}))


class SeriesReadAccessTests(BaseTestCase):
    """Список/превью/детали: кто какую серию видит."""

    def setUp(self):
        super().setUp()
        self.author = make_user("author")
        self.reader = make_user("reader")
        self.public = make_series(self.author, status="public", title="public")
        self.private = make_series(self.author, status="private", title="private")
        self.client.force_login(self.reader)

    def test_list_shows_only_own_series_and_saved_in_separate_section(self):
        own = make_series(self.reader, title="own")
        SavedQuizSeries.objects.create(user=self.reader, series=self.public)
        response = self.client.get(reverse("quizzes:quizzes_list"))
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(response.context["object_list"], [own])
        self.assertCountEqual(response.context["saved_series"], [self.public])

    def test_preview_of_foreign_public_series_is_available(self):
        response = self.client.get(reverse("quizzes:quizzes_preview", kwargs={"pk": self.public.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["nums_of_rounds"], 1)
        self.assertFalse(response.context["is_my_series"])

    def test_preview_of_foreign_private_series_is_404(self):
        response = self.client.get(reverse("quizzes:quizzes_preview", kwargs={"pk": self.private.pk}))
        self.assertEqual(response.status_code, 404)

    def test_details_of_foreign_series_is_404_even_if_public(self):
        response = self.client.get(reverse("quizzes:quizzes_details", kwargs={"pk": self.public.pk}))
        self.assertEqual(response.status_code, 404)

    def test_author_opens_preview_and_details_of_private_series(self):
        self.client.force_login(self.author)
        for name in ("quizzes:quizzes_preview", "quizzes:quizzes_details"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name, kwargs={"pk": self.private.pk}))
                self.assertEqual(response.status_code, 200)


class SeriesOwnerOnlyTests(BaseTestCase):
    """Редактирование и удаление серии/раунда - только автор, чужой pk даёт 404."""

    def setUp(self):
        super().setUp()
        self.author = make_user("author")
        self.stranger = make_user("stranger")
        self.category = make_category("Наука")
        self.series = make_series(self.author, status="public", rounds=2, title="Старое")
        self.round = self.series.rounds.order_by("round_order").first()

    def test_author_updates_main_info_including_status(self):
        self.client.force_login(self.author)
        url = reverse("quizzes:quiz_update_main_info", kwargs={"pk": self.series.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {
            "title": "Новое",
            "description": "Описание",
            "category": self.category.pk,
            "status": "private",
        })
        self.assertRedirects(
            response, reverse("quizzes:quizzes_preview", kwargs={"pk": self.series.pk}),
            fetch_redirect_response=False,
        )
        self.series.refresh_from_db()
        self.assertEqual(self.series.title, "Новое")
        self.assertEqual(self.series.description, "Описание")
        self.assertEqual(self.series.category, self.category)
        self.assertEqual(self.series.status, "private")

    def test_stranger_cannot_update_main_info(self):
        self.client.force_login(self.stranger)
        url = reverse("quizzes:quiz_update_main_info", kwargs={"pk": self.series.pk})
        self.assertEqual(self.client.get(url).status_code, 404)
        response = self.client.post(url, {"title": "Взлом", "category": self.category.pk, "status": "private"})
        self.assertEqual(response.status_code, 404)
        self.series.refresh_from_db()
        self.assertEqual(self.series.title, "Старое")

    def test_author_deletes_series_with_all_rounds(self):
        self.client.force_login(self.author)
        response = self.client.post(reverse("quizzes:quiz_delete", kwargs={"pk": self.series.pk}))
        self.assertRedirects(response, reverse("quizzes:quizzes_list"), fetch_redirect_response=False)
        self.assertFalse(QuizSeries.objects.filter(pk=self.series.pk).exists())
        self.assertFalse(Quiz.objects.exists())
        self.assertFalse(Question.objects.exists())

    def test_stranger_cannot_delete_series(self):
        self.client.force_login(self.stranger)
        url = reverse("quizzes:quiz_delete", kwargs={"pk": self.series.pk})
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertTrue(QuizSeries.objects.filter(pk=self.series.pk).exists())

    def test_author_deletes_single_round_and_series_stays(self):
        self.client.force_login(self.author)
        response = self.client.post(reverse("quizzes:round_delete", kwargs={"pk": self.round.pk}))
        self.assertRedirects(
            response, reverse("quizzes:quizzes_details", kwargs={"pk": self.series.pk}),
            fetch_redirect_response=False,
        )
        self.assertFalse(Quiz.objects.filter(pk=self.round.pk).exists())
        self.assertEqual(self.series.rounds.count(), 1)

    def test_stranger_cannot_delete_or_update_round(self):
        self.client.force_login(self.stranger)
        for name in ("quizzes:round_delete", "quizzes:round_update"):
            with self.subTest(name=name):
                url = reverse(name, kwargs={"pk": self.round.pk})
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, round_data()).status_code, 404)
        self.assertTrue(Quiz.objects.filter(pk=self.round.pk).exists())


class QuizCreateViewTests(BaseTestCase):
    """Ручное создание: новая серия с первым раундом и раунд в существующую серию."""

    def setUp(self):
        super().setUp()
        self.user = make_user("author")
        self.category = make_category()
        self.client.force_login(self.user)

    def series_data(self, **kwargs) -> dict:
        data = round_data(title="Мой квиз", category=self.category.pk, description="Описание", status="public")
        data.update(kwargs)
        return data

    def test_get_renders_form_with_question_formset(self):
        response = self.client.get(reverse("quizzes:quizzes_create"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("question_formset", response.context)

    def test_creates_series_round_questions_and_options(self):
        data = self.series_data()
        data.update(question_formset_data([{"correct_index": "2"}, {"correct_index": "0"}]))
        response = self.client.post(reverse("quizzes:quizzes_create"), data)

        series = QuizSeries.objects.get()
        self.assertRedirects(
            response, reverse("quizzes:quizzes_preview", kwargs={"pk": series.pk}),
            fetch_redirect_response=False,
        )
        self.assertEqual((series.title, series.user, series.status, series.category),
                         ("Мой квиз", self.user, "public", self.category))

        quiz = Quiz.objects.get()
        self.assertEqual((quiz.series, quiz.user, quiz.type, quiz.round_order, quiz.time_limit_seconds),
                         (series, self.user, "by_user", 0, 30))
        self.assertEqual(quiz.questions.count(), 2)
        self.assertEqual(AnswerOption.objects.count(), 8)

        first_question = quiz.questions.get(order=0)
        self.assertEqual(first_question.options.count(), 4)
        correct = first_question.options.get(is_correct=True)
        self.assertEqual((correct.order, correct.text), (2, "C"))

    def test_adds_round_to_existing_series_with_next_round_order(self):
        series = make_series(self.user, rounds=1)
        data = round_data()
        data.update(question_formset_data([{}, {}]))
        response = self.client.post(
            reverse("quizzes:quizzes_create_for_series", kwargs={"series_id": series.pk}), data,
        )
        self.assertRedirects(
            response, reverse("quizzes:quizzes_details", kwargs={"pk": series.pk}),
            fetch_redirect_response=False,
        )
        self.assertEqual(QuizSeries.objects.count(), 1)
        new_round = series.rounds.order_by("round_order").last()
        self.assertEqual((new_round.round_order, new_round.subject), (1, "Тема раунда"))

    def test_less_than_two_questions_is_rejected(self):
        data = self.series_data()
        data.update(question_formset_data([{}]))
        response = self.client.post(reverse("quizzes:quizzes_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(QuizSeries.objects.exists())
        self.assertFalse(Quiz.objects.exists())

    def test_duplicate_question_order_is_rejected(self):
        data = self.series_data()
        data.update(question_formset_data([{"order": "1"}, {"order": "1"}]))
        response = self.client.post(reverse("quizzes:quizzes_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Quiz.objects.exists())

    def test_invalid_main_form_creates_nothing(self):
        data = self.series_data(title="")
        data.update(question_formset_data([{}, {}]))
        response = self.client.post(reverse("quizzes:quizzes_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(QuizSeries.objects.exists())
        self.assertFalse(Question.objects.exists())

    def test_question_marked_for_delete_is_not_saved(self):
        data = self.series_data()
        data.update(question_formset_data([{}, {}, {"DELETE": "on"}]))
        self.client.post(reverse("quizzes:quizzes_create"), data)
        self.assertEqual(Question.objects.count(), 2)
        self.assertEqual(AnswerOption.objects.count(), 8)


class RoundUpdateViewTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("author")
        self.series = make_series(self.user, rounds=1, questions=2)
        self.round = self.series.rounds.get()
        self.url = reverse("quizzes:round_update", kwargs={"pk": self.round.pk})
        self.client.force_login(self.user)

    def existing_questions_data(self, **first_question_overrides) -> dict:
        questions = []
        for question in self.round.questions.order_by("order"):
            questions.append({
                "id": str(question.pk),
                "quiz": str(self.round.pk),
                "text": question.text,
                "order": str(question.order),
            })
        questions[0].update(first_question_overrides)
        return question_formset_data(questions, initial=len(questions))

    def test_get_prefills_options_of_existing_questions(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # форма раунда, а не серии: полей серии в ней быть не должно
        self.assertIn("subject", response.context["form"].fields)
        self.assertNotIn("title", response.context["form"].fields)
        first_form = response.context["question_formset"].forms[0]
        self.assertEqual(first_form.fields["option_1"].initial, "Вариант 0")
        self.assertEqual(first_form.fields["correct_index"].initial, 0)

    def test_updates_round_fields_and_rebuilds_options(self):
        data = round_data(subject="Новая тема", time_limit_seconds="15")
        data.update(self.existing_questions_data(text="Новый текст", option_1="Новый A", correct_index="3"))
        response = self.client.post(self.url, data)
        self.assertRedirects(
            response, reverse("quizzes:quizzes_details", kwargs={"pk": self.series.pk}),
            fetch_redirect_response=False,
        )
        self.round.refresh_from_db()
        self.assertEqual((self.round.subject, self.round.time_limit_seconds), ("Новая тема", 15))
        # серия и позиция раунда не меняются
        self.assertEqual((self.round.series, self.round.round_order), (self.series, 0))

        self.assertEqual(self.round.questions.count(), 2)
        first_question = self.round.questions.get(order=0)
        self.assertEqual(first_question.text, "Новый текст")
        # варианты пересоздаются, а не добавляются к старым
        self.assertEqual(first_question.options.count(), 4)
        self.assertEqual(first_question.options.get(order=0).text, "Новый A")
        self.assertEqual(first_question.options.get(is_correct=True).order, 3)
        self.assertEqual(AnswerOption.objects.count(), 8)

    def test_deleting_question_below_minimum_is_rejected(self):
        data = round_data()
        data.update(self.existing_questions_data(DELETE="on"))
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.round.questions.count(), 2)


class CreateQuizFromAnyDataTests(BaseTestCase):
    """quizzes.services.create_quiz_from_any_data - сохранение результата AI-генерации."""

    QUESTIONS = [
        {"text": "Q1", "fact": "F1", "options": ["a", "b", "c", "d"], "correct_index": 1},
        {"text": "Q2", "fact": "", "options": ["e", "f", "g", "h"], "correct_index": 3},
    ]

    def setUp(self):
        super().setUp()
        self.user = make_user("author")
        self.gen_request = GenerationRequest.objects.create(
            user=self.user, title="AI квиз", subject="Космос", category=make_category(),
            description="Описание", questions=2, level="pro", audience="teens", style="humorous",
            quiz_status="public", time_limit_seconds=20,
        )

    def test_creates_new_series_with_round(self):
        quiz = create_quiz_from_any_data(self.gen_request, self.QUESTIONS)
        series = quiz.series
        self.assertEqual((series.title, series.user, series.status, series.description),
                         ("AI квиз", self.user, "public", "Описание"))
        self.assertEqual(
            (quiz.type, quiz.subject, quiz.level, quiz.audience, quiz.style, quiz.time_limit_seconds, quiz.round_order),
            ("ai", "Космос", "pro", "teens", "humorous", 20, 0),
        )
        first, second = quiz.questions.order_by("order")
        self.assertEqual((first.text, first.fact), ("Q1", "F1"))
        self.assertEqual(first.options.get(is_correct=True).text, "b")
        self.assertEqual(second.options.get(is_correct=True).text, "h")
        self.assertEqual(first.options.count(), 4)

    def test_adds_round_to_existing_series(self):
        series = make_series(self.user, rounds=2)
        quiz = create_quiz_from_any_data(self.gen_request, self.QUESTIONS, series_id=series.pk)
        self.assertEqual(quiz.series, series)
        self.assertEqual(quiz.round_order, 2)
        self.assertEqual(QuizSeries.objects.count(), 1)

    def test_foreign_series_id_is_rejected_and_nothing_is_created(self):
        foreign = make_series(make_user("stranger"), rounds=1)
        with self.assertRaises(QuizSeries.DoesNotExist):
            create_quiz_from_any_data(self.gen_request, self.QUESTIONS, series_id=foreign.pk)
        self.assertEqual(foreign.rounds.count(), 1)
        self.assertEqual(Quiz.objects.count(), 1)
