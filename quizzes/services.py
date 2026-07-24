from typing import List, Any

from .models import Quiz, Question, AnswerOption, Category
from ai_generator.models import GenerationRequest

def create_quiz_from_any_data(gen_request: GenerationRequest, questions_data: List[dict]):

    quiz = Quiz.objects.create(
        user=gen_request.user,
        title=gen_request.title,
        type="ai",
        category=gen_request.category,
        subject=gen_request.subject,
        level=gen_request.level,
        style=gen_request.style
    )
    for i_index, i_question in enumerate(questions_data):
        question = Question.objects.create(
            quiz=quiz,
            text = i_question["text"],
            order = i_index,
            fact = i_question["fact"]
        )
        for i_index, i_option in i_question["options"]:
            answer = AnswerOption.objects.create(
                question=question,
                text = i_question["options"][i_index],
                is_correct = i_index == i_question["correct_index"],
                order = i_index
            )