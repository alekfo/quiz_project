from typing import List, Any

from django.db import transaction

from .models import Quiz, Question, AnswerOption, Category
from ai_generator.models import GenerationRequest


@transaction.atomic
def create_quiz_from_any_data(gen_request: GenerationRequest, questions_data: List[dict]) -> Quiz:

    quiz = Quiz.objects.create(
        user=gen_request.user,
        title=gen_request.title,
        type="ai",
        category=gen_request.category,
        subject=gen_request.subject,
        level=gen_request.level,
        style=gen_request.style,
        audience=gen_request.audience
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