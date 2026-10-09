import json
from unittest import mock

import requests
from django.test import SimpleTestCase
from django.urls import reverse

from quiz_project.testing import BaseTestCase, make_category, make_series, make_user
from quizzes.models import AnswerOption, Question, Quiz, QuizSeries

from . import prompts
from .models import GenerationRequest
from .views import _questions_to_initial

FAKE_RESULT = {
    "quiz_title": "Космос",
    "questions": [
        {"id": 1, "question": "Q1", "options": ["a", "b", "c", "d"], "correct_answer_index": "1", "fact": "F1"},
        {"id": 2, "question": "Q2", "options": ["e", "f", "g", "h"], "correct_answer_index": 3, "fact": ""},
    ],
}


def api_response(text: str) -> mock.Mock:
    response = mock.Mock()
    response.json.return_value = {"response": text}
    return response


@mock.patch("ai_generator.prompts.time.sleep")
@mock.patch("ai_generator.prompts.requests.post")
class AskTests(SimpleTestCase):
    """prompts._ask - обёртка над HTTP-прокси к Claude: повторы и обработка пустого ответа."""

    def test_returns_text_and_sends_prompt_with_api_key(self, post, sleep):
        post.return_value = api_response("ответ")
        with self.settings(CLAUDE_API_SERVICE_URL="https://proxy.example/ask", CLAUDE_API_SERVICE_KEY="secret"):
            self.assertEqual(prompts._ask("вопрос"), "ответ")
        post.assert_called_once_with(
            "https://proxy.example/ask", json={"prompt": "вопрос"}, headers={"X-API-Key": "secret"}, timeout=90,
        )
        sleep.assert_not_called()

    def test_retries_on_network_error_and_then_succeeds(self, post, sleep):
        post.side_effect = [requests.ConnectionError("down"), api_response("ответ")]
        self.assertEqual(prompts._ask("вопрос"), "ответ")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_network_error_is_raised_after_all_retries(self, post, sleep):
        post.side_effect = requests.ConnectionError("down")
        with self.assertRaises(requests.RequestException):
            prompts._ask("вопрос")
        self.assertEqual(post.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_http_error_status_is_retried(self, post, sleep):
        failed = mock.Mock()
        failed.raise_for_status.side_effect = requests.HTTPError("500")
        post.side_effect = [failed, api_response("ответ")]
        self.assertEqual(prompts._ask("вопрос"), "ответ")

    def test_empty_response_raises_runtime_error_after_retries(self, post, sleep):
        post.return_value = api_response("   ")
        with self.assertRaises(RuntimeError):
            prompts._ask("вопрос")
        self.assertEqual(post.call_count, 3)


class ParseJsonTests(SimpleTestCase):
    def test_plain_json(self):
        self.assertEqual(prompts._parse_json(' {"a": 1} '), {"a": 1})

    def test_json_in_markdown_fence(self):
        self.assertEqual(prompts._parse_json('Вот ответ:\n```json\n{"a": [1, 2]}\n```\nГотово'), {"a": [1, 2]})
        self.assertEqual(prompts._parse_json('```\n{"a": 1}\n```'), {"a": 1})

    def test_invalid_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            prompts._parse_json("не json")


class GenerateQuizQuestionsTests(SimpleTestCase):
    def test_builds_prompt_from_parameters_and_parses_answer(self):
        with mock.patch("ai_generator.prompts._ask", return_value=f"```json\n{json.dumps(FAKE_RESULT)}\n```") as ask:
            result = prompts.generate_quiz_questions({
                "quiz_title": "Мой квиз", "quiz_category": "Наука", "quiz_subject": "Чёрные дыры",
                "quiz_questions": 7, "quiz_level": "pro", "quiz_audience": "teens", "question_style": "humorous",
            })
        self.assertEqual(result, FAKE_RESULT)
        prompt = ask.call_args.args[0]
        for fragment in ("Мой квиз", "Наука", "Чёрные дыры", "Сгенерируй 7 вопросов", "Уровень сложности вопросов: Pro"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, prompt)


class QuestionsToInitialTests(SimpleTestCase):
    def test_maps_claude_format_to_form_fields(self):
        self.assertEqual(_questions_to_initial(FAKE_RESULT["questions"]), [
            {"question": "Q1", "option_1": "a", "option_2": "b", "option_3": "c", "option_4": "d",
             "correct_index": 1, "fact": "F1"},
            {"question": "Q2", "option_1": "e", "option_2": "f", "option_3": "g", "option_4": "h",
             "correct_index": 3, "fact": ""},
        ])

    def test_missing_options_become_empty_strings(self):
        initial = _questions_to_initial([{"question": "Q", "options": ["a"], "correct_answer_index": 0}])
        self.assertEqual(
            (initial[0]["option_1"], initial[0]["option_2"], initial[0]["option_4"], initial[0]["fact"]),
            ("a", "", "", ""),
        )


class AiGeneratorViewTestCase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("author")
        self.category = make_category("Наука")
        self.client.force_login(self.user)

    def round_params(self, **kwargs) -> dict:
        data = {
            "subject": "Чёрные дыры", "questions": "2", "level": "pro", "audience": "teens",
            "style": "humorous", "time_limit_seconds": "25", "points_per_correct": "3",
        }
        data.update(kwargs)
        return data

    def series_params(self, **kwargs) -> dict:
        return self.round_params(
            title="AI квиз", category=str(self.category.pk), description="Описание", quiz_status="public", **kwargs,
        )


class IndexViewTests(AiGeneratorViewTestCase):
    url = reverse("ai_generator:index")

    def generate(self, data, url=None, **mock_kwargs):
        mock_kwargs.setdefault("return_value", FAKE_RESULT)
        with mock.patch("ai_generator.views.generate_quiz_questions", **mock_kwargs) as generate:
            response = self.client.post(url or self.url, data)
        return response, generate

    def test_login_required(self):
        self.client.logout()
        self.assertLoginRequired(self.client.get(self.url))
        self.assertLoginRequired(self.client.post(reverse("ai_generator:save")))

    def test_get_renders_form_with_series_fields(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("title", response.context["form"].fields)

    def test_get_for_existing_series_has_only_round_fields(self):
        series = make_series(self.user)
        response = self.client.get(reverse("ai_generator:index_for_series", kwargs={"series_id": series.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("title", response.context["form"].fields)
        self.assertIn("subject", response.context["form"].fields)

    def test_successful_generation_saves_request_and_renders_question_formset(self):
        response, generate = self.generate(self.series_params())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ai_generator/temp_ai_quiz.html")

        # в Claude уходит название категории, а не её pk
        self.assertEqual(generate.call_args.args[0], {
            "quiz_title": "AI квиз", "quiz_category": "Наука", "quiz_subject": "Чёрные дыры",
            "quiz_questions": 2, "quiz_level": "pro", "quiz_audience": "teens", "question_style": "humorous",
        })

        gen_request = GenerationRequest.objects.get()
        self.assertEqual(
            (gen_request.user, gen_request.title, gen_request.category, gen_request.status,
             gen_request.quiz_status, gen_request.time_limit_seconds, gen_request.points_per_correct,
             gen_request.result),
            (self.user, "AI квиз", self.category, "completed", "public", 25, 3, FAKE_RESULT),
        )
        self.assertEqual(response.context["gen_request"], gen_request)
        self.assertIsNone(response.context["series_id"])
        formset = response.context["formset"]
        self.assertEqual(len(formset.forms), 2)
        self.assertEqual(formset.forms[0].initial["option_2"], "b")
        # сами вопросы до подтверждения пользователем в quizzes не попадают
        self.assertFalse(Quiz.objects.exists())

    def test_generation_for_existing_series_takes_title_and_category_from_series(self):
        series = make_series(self.user, status="private", title="Серия", category=self.category)
        url = reverse("ai_generator:index_for_series", kwargs={"series_id": series.pk})
        response, generate = self.generate(self.round_params(), url=url)
        self.assertEqual(response.context["series_id"], series.pk)
        self.assertEqual(generate.call_args.args[0]["quiz_title"], "Серия")
        gen_request = GenerationRequest.objects.get()
        self.assertEqual((gen_request.title, gen_request.category, gen_request.quiz_status),
                         ("Серия", self.category, "private"))

    def test_generation_failure_shows_form_again_and_saves_nothing(self):
        errors = (requests.ConnectionError("down"), RuntimeError("empty"), json.JSONDecodeError("bad", "", 0))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                response, _ = self.generate(self.series_params(), side_effect=error)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "ai_generator/ai_generator_index.html")
        self.assertFalse(GenerationRequest.objects.exists())

    def test_points_per_correct_out_of_range_does_not_call_claude(self):
        series = make_series(self.user)
        series_url = reverse("ai_generator:index_for_series", kwargs={"series_id": series.pk})
        for points in ("0", "1000", ""):
            # обе формы: новый квиз и раунд в существующую серию
            for url, data in ((self.url, self.series_params(points_per_correct=points)),
                              (series_url, self.round_params(points_per_correct=points))):
                with self.subTest(points=points, url=url):
                    response, generate = self.generate(data, url=url)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("points_per_correct", response.context["form"].errors)
                    generate.assert_not_called()
        self.assertFalse(GenerationRequest.objects.exists())

    def test_points_per_correct_range_is_1_to_100_inclusive(self):
        for points, accepted in (("1", True), ("100", True), ("101", False)):
            with self.subTest(points=points):
                response, generate = self.generate(self.series_params(points_per_correct=points))
                self.assertEqual(generate.called, accepted)
                self.assertEqual(
                    GenerationRequest.objects.filter(points_per_correct=int(points)).exists(), accepted,
                )

    def test_invalid_form_does_not_call_claude(self):
        response, generate = self.generate(self.series_params(questions="0"))
        self.assertEqual(response.status_code, 200)
        generate.assert_not_called()
        self.assertFalse(GenerationRequest.objects.exists())


class SaveViewTests(AiGeneratorViewTestCase):
    url = reverse("ai_generator:save")

    def setUp(self):
        super().setUp()
        self.gen_request = GenerationRequest.objects.create(
            user=self.user, title="AI квиз", subject="Чёрные дыры", category=self.category,
            description="Описание", questions=2, level="pro", audience="teens", style="humorous",
            quiz_status="public", time_limit_seconds=25, points_per_correct=6, result=FAKE_RESULT,
            status="completed",
        )

    def formset_data(self, questions=None, **extra) -> dict:
        questions = questions if questions is not None else [
            {"question": "Q1 (правка)", "option_1": "a", "option_2": "b", "option_3": "c", "option_4": "d",
             "correct_index": "1", "fact": "F1"},
            {"question": "Q2", "option_1": "e", "option_2": "f", "option_3": "g", "option_4": "h",
             "correct_index": "3", "fact": ""},
        ]
        data = {
            "generation_request_id": str(self.gen_request.pk),
            "form-TOTAL_FORMS": str(len(questions)),
            "form-INITIAL_FORMS": str(len(questions)),
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for i, question in enumerate(questions):
            for name, value in question.items():
                data[f"form-{i}-{name}"] = value
        data.update(extra)
        return data

    def test_saves_new_series_with_ai_round(self):
        response = self.client.post(self.url, self.formset_data())
        series = QuizSeries.objects.get()
        self.assertRedirects(
            response, reverse("quizzes:quizzes_preview", kwargs={"pk": series.pk}), fetch_redirect_response=False,
        )
        self.assertEqual((series.title, series.user, series.status, series.category),
                         ("AI квиз", self.user, "public", self.category))
        quiz = series.rounds.get()
        self.assertEqual((quiz.type, quiz.subject, quiz.level, quiz.time_limit_seconds, quiz.points_per_correct,
                          quiz.round_order),
                         ("ai", "Чёрные дыры", "pro", 25, 6, 0))
        first, second = quiz.questions.order_by("order")
        # сохраняется то, что отредактировал пользователь, а не исходный ответ Claude
        self.assertEqual((first.text, first.fact), ("Q1 (правка)", "F1"))
        self.assertEqual(first.options.get(is_correct=True).text, "b")
        self.assertEqual(second.options.get(is_correct=True).text, "h")
        self.assertEqual(AnswerOption.objects.count(), 8)

    def test_saves_round_into_existing_series(self):
        series = make_series(self.user, rounds=1)
        response = self.client.post(self.url, self.formset_data(series_id=str(series.pk)))
        self.assertRedirects(
            response, reverse("quizzes:quizzes_details", kwargs={"pk": series.pk}), fetch_redirect_response=False,
        )
        self.assertEqual(QuizSeries.objects.count(), 1)
        new_round = series.rounds.get(round_order=1)
        self.assertEqual(new_round.type, "ai")

    def test_foreign_generation_request_is_404(self):
        self.client.force_login(make_user("stranger"))
        response = self.client.post(self.url, self.formset_data())
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Quiz.objects.exists())

    def test_invalid_formset_saves_nothing(self):
        data = self.formset_data()
        data["form-0-option_1"] = ""
        response = self.client.post(self.url, data)
        self.assertRedirects(response, reverse("ai_generator:index"), fetch_redirect_response=False)
        self.assertFalse(QuizSeries.objects.exists())
        self.assertFalse(Question.objects.exists())

    def test_get_redirects_to_index(self):
        self.assertRedirects(self.client.get(self.url), reverse("ai_generator:index"), fetch_redirect_response=False)
