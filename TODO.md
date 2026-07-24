# TODO

Список найден по ревью сессии от 2026-07-24 (AI-генерация викторины: formset для правки вопросов + сохранение в quizzes). Ничего из этого ещё не исправлено в коде — расставлено по важности.

## Блокирующие (без этого flow не работает вообще)

- [ ] **`quizzes/services.py`** — `create_quiz_from_any_data` не компилируется (`SyntaxError`):
  - `text = i_question[]` (строка ~27) — незаконченное выражение, судя по контексту должно быть `text = i_option`.
  - `Question.object.create(...)` и `AnswerOption.object.create(...)` — опечатка, у Django-менеджера атрибут `objects`, не `object`.
  - `for i_index, i_option in i_question["options"]:` — `i_question["options"]` это список строк, разворачивать элемент в 2 переменные нельзя. Нужно `for i_index, i_option in enumerate(i_question["options"]):`.
  - Внутренний `i_index` затирает одноимённую переменную внешнего цикла (`for i_index, i_question in enumerate(questions_data)`) — не баг по факту (переприсваивается на следующей итерации), но переименовать во избежание путаницы (например, `opt_index`).
  - Функция ничего не `return`-ит — `quiz = create_quiz_from_any_data(...)` в `views.py` получит `None`. Добавить `return quiz`.

- [ ] **`ai_generator/views.py`** — `create_quiz_from_any_data` используется в `save()`, но нигде не импортирована (`from quizzes.services import create_quiz_from_any_data`). Сейчас `NameError` при сабмите formset'а.

- [ ] **`ai_generator/forms.py`** — `category = forms.ChoiceField(..., choices=get_all_categories())` вызывает функцию (и обращается к БД) **в момент импорта модуля**, а не лениво. Из-за этого `manage.py makemigrations`/`migrate`/любой запуск падает, если модель `Category` только что менялась и колонка ещё не смигрирована (`OperationalError: no such column`), плюс список категорий "замораживается" на момент старта процесса — новые категории не появятся в дропдауне без рестарта. Фикс: `choices=get_all_categories` (без скобок — Django поддерживает callable в `choices=` и вызывает лениво).

- [ ] **`ai_generator/views.py` → `index()`** — `request.clean_data.get(...)` — у `HttpRequest` нет атрибута `clean_data`. Нужно `form.cleaned_data.get(...)` (переменная валидированной формы, `form.is_valid()` уже отработал строчкой выше).

## Важные (работает криво / ловушка на будущее)

- [ ] **`ai_generator/forms.py` → `get_all_categories()`** — `(i_cat.title, i_cat.name)`: первый элемент кортежа — это value, который реально уходит в `<option value=...>` и в `POST`. Сейчас туда попадает свободный текст `title`, а не стабильный идентификатор. Заменить на `(i_cat.pk, i_cat.title)` и в `index()` брать `form.cleaned_data['category']` напрямую как `category_id` — без отдельного `Category.objects.filter(...)`.

- [ ] **`ai_generator/views.py` → `index()`** — `Category.objects.filter(name=...)` возвращает `QuerySet`, а `GenerationRequest.category` — `ForeignKey`, ожидает конкретный экземпляр `Category` (или `pk`). После фикса choices на `(pk, title)` эта строка вообще не нужна — весь блок с `category = Category.objects.filter(...)` можно убрать и передавать `category_id=form.cleaned_data['category']` прямо в `GenerationRequest.objects.create(...)`.

- [ ] **`ai_generator/forms.py` → `GenerationRequestForm`** — поле `category` объявлено дважды (строки ~49 и ~51), вторая перекрывает первую. Убрать первую (вместе с неиспользуемым `CATEGORY_CHOICES = [('funny', ...), ('serious', ...)]`).

- [ ] **`quizzes/models.py` → `Category`** — есть и `title`, и `name` (оба `CharField(max_length=50)`), без явной смысловой разницы. Решить: это дубль (убрать одно) или осознанное разделение (тогда описать, чем отличаются) — сейчас `get_all_categories()` использует оба поля вперемешку (value из `title`, label из `name`), что сбивает с толку.

- [ ] **`ai_generator/prompts.py`** — `'correct_answer': 'правильный ответ'` просит Claude вернуть точный текст ответа, который потом ищется в `options` через `.index()` (`ai_generator/views.py::_questions_to_initial`). Если текст не совпадёт дословно (лишний пробел, перефразировка) — сейчас это тихо (и неверно) подменяется на `correct_index = 0`, пользователь увидит отмеченным не тот вариант. Заменить на числовой `'correct_option_index'` (0..3, индекс в `options`) — убирает нечёткое сравнение текста и сокращает `_questions_to_initial`.

## Стоит сделать (не блокирует, но улучшит надёжность)

- [ ] **`quizzes/services.py`** — обернуть создание `Quiz` + все `Question`/`AnswerOption` в `django.db.transaction.atomic()`, чтобы при ошибке на N-м вопросе не оставалась наполовину сохранённая викторина.
- [ ] Нет проверки, что у каждого `Question` создаётся ровно один `AnswerOption` с `is_correct=True` — это инвариант, который сейчас держится только корректностью `correct_index` из формы, БД его не проверяет. Учитывать при написании тестов на `create_quiz_from_any_data`.
- [ ] После `migrate` таблица `Category` пустая — форма генерации не заработает, пока не создать хотя бы одну категорию (через `/admin` или `manage.py shell`).
- [ ] `STATICFILES_DIRS` указывает на несуществующую папку `static/` — создать папку или убрать из настроек (сейчас просто предупреждение, не блокирует).
