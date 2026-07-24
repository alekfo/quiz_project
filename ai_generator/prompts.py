import json
import logging
import re
import time
from collections import Counter

import requests

from django.conf import settings


def _ask(prompt: str, _retries: int = 2) -> str:
    """Отправляет промпт в Claude через HTTP-прокси. До 3 попыток с экспоненциальной паузой.

    Повторяет запрос при сетевых ошибках и при пустом ответе.
    Бросает RuntimeError если все попытки исчерпаны.
    """

    for attempt in range(_retries + 1):
        t0 = time.monotonic()
        try:
            response = requests.post(
                settings.CLAUDE_API_SERVICE_URL,
                json={"prompt": prompt},
                headers={"X-API-Key": settings.CLAUDE_API_SERVICE_KEY},
                timeout=90,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            if attempt < _retries:
                time.sleep(2 ** attempt)
                continue
            raise
        elapsed = time.monotonic() - t0
        text = response.json().get("response", "")
        if text.strip():
            return text
        if attempt < _retries:
            time.sleep(2 ** attempt)
    raise RuntimeError("Claude API returned empty response after retries")


def _parse_json(text: str) -> dict | list:
    """Парсит JSON из ответа Claude, снимая markdown-обёртку ```json``` если она есть."""
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    return json.loads(match.group(1) if match else text.strip())

_LEVEL_INSTRUCTIONS = {
    'junior': (
        'Уровень сложности вопросов: Junior, '
        'Вопросы должны быть базового уровня — преимущественно каждый на них ответит, '
    ),
    'middle': (
        'Уровень сложности вопросов: Middle, '
        'Вопросы среднего уровня — вопросы должны быть преимущественно не сложный, но уже не элементарные, на некоторые не каждый ответит, '
    ),
    'pro': (
        'Уровень сложности вопросов: Pro, '
        'Вопросы высокого уровня — все вопросы должны быть сложными, не каждый человек ответит на них, но не надо перебарщивать, тут не академики, '
    ),
    'common': (
            'Уровень сложности вопросов: Common, '
            'Пользователь не указал никакого уровня, сделай вперемешку - и сложные вопросы и простые, главное - чтобы было интересно, '
        ),
}

def generate_quiz_questions(instruction_data: dict) -> dict:
    """Генерирует 8 вопросов для интервью на основе текста вакансии и уровня кандидата.

    Возвращает dict с ключами job_title, company_name и questions (список {text, type}).
    """


    quiz_subject = instruction_data.get("quiz_subject", "")
    quiz_questions = instruction_data.get("quiz_questions", 1)
    quiz_level = instruction_data.get("quiz_level", "")
    quiz_audience = instruction_data.get("quiz_audience", "")
    question_style = instruction_data.get("question_style", "")

    level = _LEVEL_INSTRUCTIONS.get(quiz_level, '')


    text = _ask(f"""Ты эксперт по составлению вопросов к викторинам. Даю тебе вводные инструкции на генерацию вопросов викторины.

                        Тема викторины: {quiz_subject}
                        {level}
                        Аудитория для викторины: {quiz_audience}
                        Стиль вопросов викторины: {question_style}
                        
                        Сгенерируй {quiz_questions} вопросов: сделай ступенчатый переход в стиле интересных квизов.
                        Каждый новый вопрос должен быть чуть сложнее предыдущего. Избегай банальных и предсказуемых вопросов.
                        Также определи название должности и компанию из текста вакансии (если указаны).

                        Ответь строго в формате JSON:
                        {{
                          "quiz_subject": "тема викторины или пустая строка",
                          "quiz_level": "уровень сложности викторины или пустая строка",
                          "quiz_audience": "аудитория викторины или пустая строка",
                          "question_style": "стиль вопросов викторины или пустая строка",
                          "questions": [
                            {{'id': 1, 'question': 'сам вопрос', 'options': [вариант1, .., вариант4], 'correct_answer': 'правильный ответ', 'fact': 'какой-нибудь факт в стиле вопроса ({question_style})'}},
                            {{...}},
                            {{'id': {quiz_questions}, 'question': 'сам вопрос', 'options': [вариант1, .., вариант4], 'correct_answer': 'правильный ответ', 'fact': 'какой-нибудь факт в стиле вопроса ({question_style})'}},
                          ]
                        }}""")
    result = _parse_json(text)
    return result