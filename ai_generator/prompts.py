import json
from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError
import logging
import re
import time
from collections import Counter

import requests

from django.conf import settings

logger = logging.getLogger(__name__)

def _ask_deepseek(prompt: str, _retries: int = 2) -> str:

    for attempt in range(_retries + 1):
        t0 = time.monotonic()
        try:
            client = OpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com",
            )

            response = client.chat.completions.create(
                model="deepseek-flash",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt}
                ],
                stream=False,
                extra_body={"thinking": {"type": "disabled"}}  # <-- отключаем размышления
            )
        except (APIConnectionError, APIStatusError, APITimeoutError) as e:
            if attempt < _retries:
                time.sleep(2 ** attempt)
                continue
            logger.warning("Превышено количсество попыток запроса к API DeepSeek")
            raise
        elapsed = time.monotonic() - t0
        text = response.choices[0].message.content
        if text.strip():
            logger.info("Успешно получен сырой текст от API DeepSeek")
            return text
        if attempt < _retries:
            time.sleep(2 ** attempt)
    raise RuntimeError("Claude API returned empty response after retries")

def _ask_claude(prompt: str, _retries: int = 2) -> str:
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
            logger.warning("Превышено количсество попыток запроса к API Claude")
            raise
        elapsed = time.monotonic() - t0
        text = response.json().get("response", "")
        if text.strip():
            logger.info("Успешно получен сырой текст от API Claude")
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

    quiz_title= instruction_data.get("quiz_title", "")
    quiz_category = instruction_data.get("quiz_category", "")
    round_subject = instruction_data.get("round_subject", "")
    round_description_for_ai = instruction_data.get("round_description_for_ai", "")
    round_questions = instruction_data.get("round_questions", 1)
    round_level = instruction_data.get("round_level", "")
    round_audience = instruction_data.get("round_audience", "")
    round_style = instruction_data.get("round_style", "")

    level = _LEVEL_INSTRUCTIONS.get(round_level, '')

    prompt_text = f"""Ты эксперт по составлению вопросов к квизам. Даю тебе вводные инструкции на генерацию вопросов ОДНОГО из раундов квиза.
                        Наименование квиза: {quiz_title}
                        Категория квиза: {quiz_category}
                        Наименование и Категория квиза - общие для всех раундов (ты делаешь только один) - учти эти данные, 
                        но приоритетная тема вопросов из "Тема вопросов квиза" и "Описание вопросв раундов"
                        Тема вопросов раунда: {round_subject}
                        Описание вопросов раунда: {round_description_for_ai}
                        Уровень сложности вопросов раунда: {level}
                        Аудитория для вопросов раунда: {round_audience}
                        Стиль вопросов раунда: {round_style}
                        
                        Сгенерируй {round_questions} вопросов: сделай ступенчатый переход в стиле интересных квизов.
                        Каждый новый вопрос должен быть чуть сложнее предыдущего. Избегай банальных и предсказуемых вопросов.

                        Ответь строго в формате JSON:
                        {{
                          "questions": [
                            {{"id": 1, "question": "сам вопрос", "options": [вариант0, .., вариант3], "correct_answer_index": "индекс правильного ответа из списка options", "fact": "какой-нибудь факт в стиле вопроса ({round_style})"}},
                            {{...}},
                            {{"id": {round_questions}, "question": "сам вопрос", "options": [вариант0, .., вариант3], "correct_answer_index": "индекс правильного ответа из списка options", "fact": "какой-нибудь факт в стиле вопроса ({round_style})"}}
                          ]
                        }}"""


    text = _ask_deepseek(prompt_text) if settings.BASE_AI_MODEL == "deep_seek" else _ask_claude(prompt_text)
    result = _parse_json(text)
    logger.info("Успешно отпарсен ответ от BASE_AI_MODEL (%s)", settings.BASE_AI_MODEL)
    return result