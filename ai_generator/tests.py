import json
from datetime import timedelta
from unittest import mock

import requests
from django.contrib.messages import constants as message_levels, get_messages
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from quiz_project.testing import BaseTestCase, make_category, make_series, make_user
from quizzes.models import AnswerOption, Question, Quiz, QuizSeries

from . import prompts
from .models import GenerationRequest
from .views import GENERATION_STALE_AFTER, _questions_to_initial

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
        # лимит по умолчанию - 1 генерация в день; поднимаем, чтобы он не мешал тестам не про лимит
        self.user = make_user("author", ai_quizzes_limit_per_day=50)
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

    def make_gen_request(self, status="completed", user=None, **kwargs) -> GenerationRequest:
        kwargs.setdefault("result", FAKE_RESULT if status in ("completed", "saved", "discarded") else None)
        return GenerationRequest.objects.create(
            user=user or self.user, title="AI квиз", subject="Чёрные дыры", category=self.category,
            description="Описание", questions=2, level="pro", audience="teens", style="humorous",
            quiz_status="public", time_limit_seconds=25, points_per_correct=6, status=status, **kwargs,
        )

    def make_stale(self, gen_request: GenerationRequest) -> None:
        GenerationRequest.objects.filter(pk=gen_request.pk).update(
            created_at=timezone.now() - GENERATION_STALE_AFTER - timedelta(seconds=1),
        )

    def generate(self, data, url=None, **mock_kwargs):
        mock_kwargs.setdefault("return_value", FAKE_RESULT)
        with mock.patch("ai_generator.views.generate_quiz_questions", **mock_kwargs) as generate:
            response = self.client.post(url or reverse("ai_generator:index"), data)
        return response, generate

    def review_url(self, gen_request: GenerationRequest) -> str:
        return reverse("ai_generator:review", kwargs={"pk": gen_request.pk})

    def message_levels(self, response) -> list[int]:
        return [message.level for message in get_messages(response.wsgi_request)]


class GenerationRequestModelTests(AiGeneratorViewTestCase):
    def test_only_one_in_progress_request_per_user(self):
        self.make_gen_request(status="in_progress")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_gen_request(status="in_progress")
        # на другие статусы и на других пользователей замок не действует
        self.make_gen_request(status="completed")
        self.make_gen_request(status="in_progress", user=make_user("other"))


class IndexViewTests(AiGeneratorViewTestCase):
    url = reverse("ai_generator:index")

    def test_login_required(self):
        self.client.logout()
        self.assertLoginRequired(self.client.get(self.url))
        for name in ("review", "status"):
            self.assertLoginRequired(self.client.get(reverse(f"ai_generator:{name}", kwargs={"pk": 1})))
        for name in ("save", "discard"):
            self.assertLoginRequired(self.client.post(reverse(f"ai_generator:{name}", kwargs={"pk": 1})))

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

    def test_foreign_series_is_404_and_does_not_call_claude(self):
        series = make_series(make_user("stranger"), status="public")
        url = reverse("ai_generator:index_for_series", kwargs={"series_id": series.pk})
        self.assertEqual(self.client.get(url).status_code, 404)
        response, generate = self.generate(self.round_params(), url=url)
        self.assertEqual(response.status_code, 404)
        generate.assert_not_called()

    def test_successful_generation_saves_request_and_redirects_to_review(self):
        response, generate = self.generate(self.series_params())
        gen_request = GenerationRequest.objects.get()
        # ответ на POST - редирект: F5 на странице проверки не повторяет платный запрос
        self.assertRedirects(response, self.review_url(gen_request), fetch_redirect_response=False)

        # в Claude уходит название категории, а не её pk
        self.assertEqual(generate.call_args.args[0], {
            "quiz_title": "AI квиз", "quiz_category": "Наука", "quiz_subject": "Чёрные дыры",
            "quiz_questions": 2, "quiz_level": "pro", "quiz_audience": "teens", "question_style": "humorous",
        })
        self.assertEqual(
            (gen_request.user, gen_request.title, gen_request.category, gen_request.series, gen_request.status,
             gen_request.quiz_status, gen_request.time_limit_seconds, gen_request.points_per_correct,
             gen_request.result),
            (self.user, "AI квиз", self.category, None, "completed", "public", 25, 3, FAKE_RESULT),
        )
        # сами вопросы до подтверждения пользователем в quizzes не попадают
        self.assertFalse(Quiz.objects.exists())

    def test_request_is_in_progress_while_claude_is_called(self):
        seen = []

        def fake_claude(_instruction):
            gen_request = GenerationRequest.objects.get()
            seen.append((gen_request.status, gen_request.result))
            return FAKE_RESULT

        self.generate(self.series_params(), side_effect=fake_claude)
        self.assertEqual(seen, [("in_progress", None)])

    def test_generation_for_existing_series_takes_title_and_category_from_series(self):
        series = make_series(self.user, status="private", title="Серия", category=self.category)
        url = reverse("ai_generator:index_for_series", kwargs={"series_id": series.pk})
        response, generate = self.generate(self.round_params(), url=url)
        self.assertEqual(generate.call_args.args[0]["quiz_title"], "Серия")
        gen_request = GenerationRequest.objects.get()
        self.assertEqual((gen_request.title, gen_request.category, gen_request.quiz_status, gen_request.series),
                         ("Серия", self.category, "private", series))

    def test_generation_failure_shows_form_again_and_marks_request_failed(self):
        # лимит 1 в день: три неудачи подряд проходят - failed не блокирует новую попытку и лимит не тратит
        self.user.ai_quizzes_limit_per_day = 1
        self.user.save()
        errors = (requests.ConnectionError("down"), RuntimeError("empty"), json.JSONDecodeError("bad", "", 0))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                response, generate = self.generate(self.series_params(), side_effect=error)
                self.assertTrue(generate.called)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "ai_generator/ai_generator_index.html")
                self.assertIn(message_levels.ERROR, self.message_levels(response))
        self.assertEqual(list(GenerationRequest.objects.values_list("status", flat=True)), ["failed"] * 3)

    def test_answer_without_questions_is_failure(self):
        for result in ({}, {"questions": []}, ["not", "a", "dict"]):
            with self.subTest(result=result):
                response, _ = self.generate(self.series_params(), return_value=result)
                self.assertTemplateUsed(response, "ai_generator/ai_generator_index.html")
        self.assertFalse(GenerationRequest.objects.exclude(status="failed").exists())

    def test_unexpected_error_still_releases_in_progress_lock(self):
        with self.assertRaises(ZeroDivisionError):
            self.generate(self.series_params(), side_effect=ZeroDivisionError)
        self.assertEqual(GenerationRequest.objects.get().status, "failed")

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
                # иначе готовый результат не дал бы запустить следующую генерацию
                GenerationRequest.objects.update(status="saved")

    def test_invalid_form_does_not_call_claude_and_keeps_errors(self):
        response, generate = self.generate(self.series_params(questions="0"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("questions", response.context["form"].errors)
        generate.assert_not_called()
        self.assertFalse(GenerationRequest.objects.exists())


class ActiveGenerationTests(AiGeneratorViewTestCase):
    """Одна активная генерация на пользователя: in_progress / completed блокируют новую."""
    url = reverse("ai_generator:index")

    def test_unsaved_result_blocks_new_generation(self):
        gen_request = self.make_gen_request(status="completed")
        response = self.client.get(self.url)
        self.assertRedirects(response, self.review_url(gen_request), fetch_redirect_response=False)
        self.assertIn(message_levels.INFO, self.message_levels(response))

        response, generate = self.generate(self.series_params())
        self.assertRedirects(response, self.review_url(gen_request), fetch_redirect_response=False)
        generate.assert_not_called()
        self.assertEqual(GenerationRequest.objects.count(), 1)

    def test_running_generation_blocks_new_one(self):
        gen_request = self.make_gen_request(status="in_progress")
        self.assertRedirects(self.client.get(self.url), self.review_url(gen_request), fetch_redirect_response=False)
        response, generate = self.generate(self.series_params())
        self.assertRedirects(response, self.review_url(gen_request), fetch_redirect_response=False)
        generate.assert_not_called()

    def test_block_is_per_user_and_covers_series_form_too(self):
        self.make_gen_request(status="completed", user=make_user("other"))
        self.assertEqual(self.client.get(self.url).status_code, 200)

        gen_request = self.make_gen_request(status="completed")
        series = make_series(self.user)
        response = self.client.get(reverse("ai_generator:index_for_series", kwargs={"series_id": series.pk}))
        self.assertRedirects(response, self.review_url(gen_request), fetch_redirect_response=False)

    def test_finished_requests_do_not_block(self):
        for status in ("saved", "discarded", "failed"):
            self.make_gen_request(status=status)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_stale_in_progress_becomes_failed_and_form_is_shown(self):
        gen_request = self.make_gen_request(status="in_progress")
        self.make_stale(gen_request)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        gen_request.refresh_from_db()
        self.assertEqual(gen_request.status, "failed")

    def test_parallel_request_gets_message_instead_of_500(self):
        # гонка: проверка в начале index() пройдена, но in_progress-запись уже создал соседний запрос
        with mock.patch.object(GenerationRequest.objects, "create", side_effect=IntegrityError):
            response, generate = self.generate(self.series_params())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertIn(message_levels.ERROR, self.message_levels(response))
        generate.assert_not_called()


class DailyLimitTests(AiGeneratorViewTestCase):
    url = reverse("ai_generator:index")

    def setUp(self):
        super().setUp()
        self.user.ai_quizzes_limit_per_day = 2
        self.user.save()

    def assertLimitReached(self, expected_url=reverse("quizzes:menu"), url=None):
        response = self.client.get(url or self.url)
        self.assertRedirects(response, expected_url, fetch_redirect_response=False)
        self.assertIn(message_levels.ERROR, self.message_levels(response))
        response, generate = self.generate(self.series_params(), url=url)
        self.assertRedirects(response, expected_url, fetch_redirect_response=False)
        generate.assert_not_called()

    def test_saved_and_discarded_requests_spend_limit(self):
        self.make_gen_request(status="saved")
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.make_gen_request(status="discarded")
        self.assertLimitReached()

    def test_limit_for_series_form_redirects_to_series(self):
        self.make_gen_request(status="saved")
        self.make_gen_request(status="saved")
        series = make_series(self.user)
        self.assertLimitReached(
            expected_url=reverse("quizzes:quizzes_details", kwargs={"pk": series.pk}),
            url=reverse("ai_generator:index_for_series", kwargs={"series_id": series.pk}),
        )

    def test_failed_yesterdays_and_foreign_requests_do_not_count(self):
        self.make_gen_request(status="saved")
        self.make_gen_request(status="failed")
        old = self.make_gen_request(status="saved")
        GenerationRequest.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.make_gen_request(status="saved", user=make_user("other"))
        response, generate = self.generate(self.series_params())
        self.assertTrue(generate.called)

    def test_generation_itself_spends_limit(self):
        for _ in range(2):
            _, generate = self.generate(self.series_params())
            self.assertTrue(generate.called)
            GenerationRequest.objects.update(status="saved")
        self.assertLimitReached()


class ReviewAndStatusViewTests(AiGeneratorViewTestCase):
    # Рендер самих страниц (вопросы на проверку, экран ожидания) - в ReviewPagesTests ниже.

    def status_url(self, gen_request):
        return reverse("ai_generator:status", kwargs={"pk": gen_request.pk})

    def test_foreign_request_is_404(self):
        gen_request = self.make_gen_request(status="completed", user=make_user("stranger"))
        self.assertEqual(self.client.get(self.review_url(gen_request)).status_code, 404)
        self.assertEqual(self.client.get(self.status_url(gen_request), headers={"HX-Request": "true"}).status_code, 404)

    def test_review_of_saved_or_discarded_request_is_404(self):
        for status in ("saved", "discarded"):
            with self.subTest(status=status):
                self.assertEqual(self.client.get(self.review_url(self.make_gen_request(status=status))).status_code, 404)

    def test_review_of_failed_request_redirects_to_form_with_error(self):
        response = self.client.get(self.review_url(self.make_gen_request(status="failed")))
        self.assertRedirects(response, reverse("ai_generator:index"), fetch_redirect_response=False)
        self.assertIn(message_levels.ERROR, self.message_levels(response))

        series = make_series(self.user)
        response = self.client.get(self.review_url(self.make_gen_request(status="failed", series=series)))
        self.assertRedirects(
            response, reverse("ai_generator:index_for_series", kwargs={"series_id": series.pk}),
            fetch_redirect_response=False,
        )

    def test_status_while_in_progress_is_empty_204(self):
        response = self.client.get(self.status_url(self.make_gen_request(status="in_progress")),
                                   headers={"HX-Request": "true"})
        self.assertEqual(response.status_code, 204)
        self.assertNotIn("HX-Redirect", response)

    def test_status_after_generation_sends_hx_redirect_to_review(self):
        for status in ("completed", "failed"):
            with self.subTest(status=status):
                gen_request = self.make_gen_request(status=status)
                response = self.client.get(self.status_url(gen_request), headers={"HX-Request": "true"})
                self.assertEqual(response.status_code, 204)
                self.assertEqual(response["HX-Redirect"], self.review_url(gen_request))

    def test_status_marks_stale_request_failed(self):
        gen_request = self.make_gen_request(status="in_progress")
        self.make_stale(gen_request)
        response = self.client.get(self.status_url(gen_request), headers={"HX-Request": "true"})
        self.assertEqual(response["HX-Redirect"], self.review_url(gen_request))
        gen_request.refresh_from_db()
        self.assertEqual(gen_request.status, "failed")

    def test_status_without_htmx_redirects_to_review_and_post_is_not_allowed(self):
        gen_request = self.make_gen_request(status="in_progress")
        self.assertRedirects(self.client.get(self.status_url(gen_request)), self.review_url(gen_request),
                             fetch_redirect_response=False)
        self.assertEqual(self.client.post(self.status_url(gen_request)).status_code, 405)


class ReviewPagesTests(AiGeneratorViewTestCase):
    """Страницы, которые отдаёт review(): вопросы на проверку и экран ожидания."""

    def test_completed_request_renders_question_formset(self):
        gen_request = self.make_gen_request(status="completed")
        response = self.client.get(self.review_url(gen_request))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ai_generator/temp_ai_quiz.html")
        self.assertEqual(response.context["gen_request"], gen_request)
        self.assertIsNone(response.context["series_id"])
        formset = response.context["formset"]
        self.assertEqual(len(formset.forms), 2)
        self.assertEqual(formset.forms[0].initial["option_2"], "b")
        # формы ведут на адреса этой генерации; «Не сохранять» - кнопка POST-формы, а не ссылка
        save_url = reverse("ai_generator:save", kwargs={"pk": gen_request.pk})
        discard_url = reverse("ai_generator:discard", kwargs={"pk": gen_request.pk})
        self.assertContains(response, f'action="{save_url}"')
        self.assertContains(response, f'<form method="post" action="{discard_url}" id="discard-form">')
        self.assertContains(response, 'form="discard-form"')
        # обновление страницы ничего не меняет - это чтение из базы
        self.client.get(self.review_url(gen_request))
        gen_request.refresh_from_db()
        self.assertEqual(gen_request.status, "completed")

    def test_review_for_series_round_has_series_id(self):
        series = make_series(self.user)
        response = self.client.get(self.review_url(self.make_gen_request(status="completed", series=series)))
        self.assertEqual(response.context["series_id"], series.pk)

    def test_in_progress_request_renders_waiting_screen_with_status_polling(self):
        gen_request = self.make_gen_request(status="in_progress")
        response = self.client.get(self.review_url(gen_request))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ai_generator/generation_wait.html")
        status_url = reverse("ai_generator:status", kwargs={"pk": gen_request.pk})
        self.assertContains(response, f'hx-get="{status_url}"')
        # оверлей со спиннером открыт сразу, а не ждёт отправки формы
        self.assertContains(response, "data-autostart>")

    def test_stale_in_progress_request_is_not_shown_as_waiting(self):
        gen_request = self.make_gen_request(status="in_progress")
        self.make_stale(gen_request)
        response = self.client.get(self.review_url(gen_request))
        self.assertRedirects(response, reverse("ai_generator:index"), fetch_redirect_response=False)

    def test_generation_form_overlay_is_hidden_until_submit(self):
        response = self.client.get(reverse("ai_generator:index"))
        self.assertContains(response, 'id="generation-overlay"')
        self.assertContains(response, "hidden>")
        self.assertNotContains(response, "data-autostart")


class SaveViewTests(AiGeneratorViewTestCase):
    def setUp(self):
        super().setUp()
        self.gen_request = self.make_gen_request(status="completed")
        self.url = reverse("ai_generator:save", kwargs={"pk": self.gen_request.pk})

    def formset_data(self, questions=None, **extra) -> dict:
        questions = questions if questions is not None else [
            {"question": "Q1 (правка)", "option_1": "a", "option_2": "b", "option_3": "c", "option_4": "d",
             "correct_index": "1", "fact": "F1"},
            {"question": "Q2", "option_1": "e", "option_2": "f", "option_3": "g", "option_4": "h",
             "correct_index": "3", "fact": ""},
        ]
        data = {
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
        self.gen_request.refresh_from_db()
        self.assertEqual(self.gen_request.status, "saved")

    def test_saves_round_into_series_of_generation_request(self):
        series = make_series(self.user, rounds=1)
        self.gen_request.series = series
        self.gen_request.save()
        # серия берётся из GenerationRequest, а не из формы: подставленный series_id игнорируется
        other_series = make_series(self.user, title="Другая", rounds=0)
        response = self.client.post(self.url, self.formset_data(series_id=str(other_series.pk)))
        self.assertRedirects(
            response, reverse("quizzes:quizzes_details", kwargs={"pk": series.pk}), fetch_redirect_response=False,
        )
        self.assertEqual(QuizSeries.objects.count(), 2)
        self.assertEqual(series.rounds.get(round_order=1).type, "ai")
        self.assertFalse(other_series.rounds.exists())

    def test_second_save_is_404_and_creates_nothing(self):
        self.client.post(self.url, self.formset_data())
        response = self.client.post(self.url, self.formset_data())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Quiz.objects.count(), 1)

    def test_only_completed_request_can_be_saved(self):
        for status in ("in_progress", "discarded", "failed"):
            with self.subTest(status=status):
                GenerationRequest.objects.filter(pk=self.gen_request.pk).update(status=status)
                self.assertEqual(self.client.post(self.url, self.formset_data()).status_code, 404)
        self.assertFalse(Quiz.objects.exists())

    def test_foreign_generation_request_is_404(self):
        self.client.force_login(make_user("stranger"))
        response = self.client.post(self.url, self.formset_data())
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Quiz.objects.exists())

    def test_invalid_formset_shows_review_page_again_with_user_edits(self):
        data = self.formset_data()
        data["form-0-option_1"] = ""
        data["form-1-question"] = "Q2 (моя правка)"
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ai_generator/temp_ai_quiz.html")
        formset = response.context["formset"]
        self.assertIn("option_1", formset.forms[0].errors)
        # правки пользователя не теряются, генерация остаётся ждать сохранения
        self.assertContains(response, "Q2 (моя правка)")
        self.assertIn(message_levels.ERROR, self.message_levels(response))
        self.assertFalse(QuizSeries.objects.exists())
        self.assertFalse(Question.objects.exists())
        self.gen_request.refresh_from_db()
        self.assertEqual(self.gen_request.status, "completed")

    def test_get_redirects_to_review(self):
        self.assertRedirects(self.client.get(self.url), self.review_url(self.gen_request),
                             fetch_redirect_response=False)


class DiscardViewTests(AiGeneratorViewTestCase):
    def discard_url(self, gen_request):
        return reverse("ai_generator:discard", kwargs={"pk": gen_request.pk})

    def test_discard_marks_request_and_unblocks_new_generation(self):
        gen_request = self.make_gen_request(status="completed")
        response = self.client.post(self.discard_url(gen_request))
        self.assertRedirects(response, reverse("quizzes:menu"), fetch_redirect_response=False)
        gen_request.refresh_from_db()
        self.assertEqual(gen_request.status, "discarded")
        self.assertFalse(Quiz.objects.exists())
        self.assertEqual(self.client.get(reverse("ai_generator:index")).status_code, 200)

    def test_discard_of_round_returns_to_series(self):
        series = make_series(self.user)
        gen_request = self.make_gen_request(status="completed", series=series)
        response = self.client.post(self.discard_url(gen_request))
        self.assertRedirects(
            response, reverse("quizzes:quizzes_details", kwargs={"pk": series.pk}), fetch_redirect_response=False,
        )

    def test_get_is_not_allowed(self):
        gen_request = self.make_gen_request(status="completed")
        self.assertEqual(self.client.get(self.discard_url(gen_request)).status_code, 405)
        gen_request.refresh_from_db()
        self.assertEqual(gen_request.status, "completed")

    def test_foreign_or_not_completed_request_is_404(self):
        foreign = self.make_gen_request(status="completed", user=make_user("stranger"))
        self.assertEqual(self.client.post(self.discard_url(foreign)).status_code, 404)
        for status in ("in_progress", "saved", "failed"):
            with self.subTest(status=status):
                self.assertEqual(self.client.post(self.discard_url(self.make_gen_request(status=status))).status_code, 404)
