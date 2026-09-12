from typing import List, Any

from django.db import transaction

from .models import Quiz, Question, AnswerOption, Category, QuizSeries
from ai_generator.models import GenerationRequest


@transaction.atomic
def create_quiz_from_any_data(gen_request: GenerationRequest, questions_data: List[dict], series_id=None) -> Quiz:
    """
    Создаёт один раунд (Quiz) из результата AI-генерации и подвешивает его на
    QuizSeries - ровно та же двухветочная логика, что и в QuizCreateView.forms_valid
    (quizzes/views.py), только на стороне ai_generator: series_id пришёл ->
    раунд добавляется в существующую серию (владение проверяется прямо в
    .get(pk=series_id, user=...) - чужой series_id даст DoesNotExist, а не тихо
    привяжет раунд не туда); series_id нет -> под этот раунд создаётся новая
    QuizSeries с тем же title, что у сгенерированного квиза.
    round_order=series.rounds.count() - номер раунда по порядку прямо на
    создании, отдельного счётчика на QuizSeries не заводили.
    """
    if series_id:
        series = QuizSeries.objects.get(pk=series_id, user=gen_request.user)
    else:
        series = QuizSeries.objects.create(
            title=gen_request.title,
            user=gen_request.user
        )

    quiz = Quiz.objects.create(
        user=gen_request.user,
        series=series,
        title=gen_request.title,
        type="ai",
        category=gen_request.category,
        subject=gen_request.subject,
        level=gen_request.level,
        style=gen_request.style,
        audience=gen_request.audience,
        round_order=series.rounds.count()
    )
    for i_index, i_question in enumerate(questions_data):
        question = Question.objects.create(
            quiz=quiz,
            text = i_question["text"],
            order = i_index,
            fact = i_question["fact"]
        )
        for opt_index, i_option in enumerate(i_question["options"]):
            answer = AnswerOption.objects.create(
                question=question,
                text = i_option,
                is_correct = opt_index == i_question["correct_index"],
                order = opt_index
            )

    return quiz