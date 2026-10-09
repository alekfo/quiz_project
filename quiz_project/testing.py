"""
Общая база для тестов всех приложений: подмена настроек, небезопасных для тестов,
и фабрики тестовых данных.

Зачем подмена настроек:
  - CACHES: боевой кэш - FileBasedCache на диске, общий с runserver. Тесты логина/
    регистрации без подмены оставили бы счётчики rate-limit в этих файлах и
    заблокировали бы вход на параллельно запущенном dev-сервере.
  - CHANNEL_LAYERS: боевой слой - Redis, которого нет ни в CI, ни обязательно локально.
  - PASSWORD_HASHERS: MD5 вместо PBKDF2 - создание пользователей в разы быстрее.

override_settings на базовом классе наследуется всеми его потомками, поэтому
обычный `python manage.py test` безопасен без отдельного файла настроек.
"""
import logging

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from quizzes.models import AnswerOption, Category, Question, Quiz, QuizSeries

TEST_SETTINGS = {
    "CACHES": {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    },
    "CHANNEL_LAYERS": {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    },
    "PASSWORD_HASHERS": ["django.contrib.auth.hashers.MD5PasswordHasher"],
}

PASSWORD = "test-pass-12345"


def make_user(username: str, **kwargs):
    kwargs.setdefault("email", f"{username}@example.com")
    return get_user_model().objects.create_user(username=username, password=PASSWORD, **kwargs)


def make_category(title: str = "История") -> Category:
    return Category.objects.create(title=title)


def make_round(series: QuizSeries, round_order: int = 0, questions: int = 2, **kwargs) -> Quiz:
    """Раунд (Quiz) с вопросами; у каждого вопроса 4 варианта, правильный - первый (order=0)."""
    kwargs.setdefault("time_limit_seconds", 50)
    quiz = Quiz.objects.create(
        user=series.user,
        series=series,
        type="by_user",
        subject=f"Раунд {round_order}",
        level="common",
        style="serious",
        round_order=round_order,
        **kwargs,
    )
    for q_order in range(questions):
        question = Question.objects.create(quiz=quiz, text=f"Вопрос {q_order}", order=q_order)
        for o_order in range(4):
            AnswerOption.objects.create(
                question=question,
                text=f"Вариант {o_order}",
                is_correct=(o_order == 0),
                order=o_order,
            )
    return quiz


def make_series(user, status: str = "private", rounds: int = 1, questions: int = 2,
                title: str = "Квиз", category: Category | None = None) -> QuizSeries:
    series = QuizSeries.objects.create(
        title=title,
        user=user,
        category=category or Category.objects.first() or make_category(),
        status=status,
    )
    for round_order in range(rounds):
        make_round(series, round_order=round_order, questions=questions)
    return series


def correct_option(question: Question) -> AnswerOption:
    return question.options.get(is_correct=True)


def wrong_option(question: Question) -> AnswerOption:
    return question.options.filter(is_correct=False).first()


@override_settings(**TEST_SETTINGS)
class BaseTestCase(TestCase):
    """Базовый класс для тестов проекта - см. докстринг модуля."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ожидаемые 403/404 и INFO-логи вьюх иначе заливают вывод тестов
        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)

    def setUp(self):
        super().setUp()
        # LocMemCache живёт в памяти процесса и между тестами сам не очищается -
        # без этого счётчики rate-limit перетекали бы из теста в тест
        cache.clear()

    def assertLoginRequired(self, response):
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith("/users/login/"),
            f"ожидался редирект на логин, получен {response['Location']}",
        )
