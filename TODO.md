# TODO

Список найден по ревью сессии от 2026-07-24 (AI-генерация викторины: formset для правки вопросов + сохранение в quizzes). Обновлено по ревью сессии от 2026-07-27 — большая часть блокирующих и важных пунктов исправлена.

## Блокирующие (без этого flow не работает вообще)

- [x] **`quizzes/services.py`** — `create_quiz_from_any_data` не компилировался (`SyntaxError`, `object`/`objects`, разворачивание `options` без `enumerate`, отсутствующий `return`). Исправлено: `enumerate()`, `Question.objects`/`AnswerOption.objects`, `return quiz`, внутренний индекс переименован в `opt_index`.

- [x] **`ai_generator/views.py`** — `create_quiz_from_any_data` не была импортирована. Импорт добавлен (`from quizzes.services import create_quiz_from_any_data`).

- [x] **`ai_generator/forms.py`** — `choices=get_all_categories()` вызывался в момент импорта модуля. Исправлено на ленивый `choices=get_all_categories` (без скобок).

- [x] **`ai_generator/views.py` → `index()`** — `request.clean_data.get(...)` заменено на использование `form.cleaned_data` через `GenerationRequestForm`.

## Важные (работает криво / ловушка на будущее)

- [x] **`ai_generator/forms.py` → `get_all_categories()`** — заменено на `(i_cat.pk, i_cat.title)`.

- [x] **`ai_generator/views.py` → `index()`** — блок с `Category.objects.filter(...)` убран, категория передаётся напрямую.

- [x] **`ai_generator/forms.py` → `GenerationRequestForm`** — дублирующееся поле `category` и неиспользуемый `CATEGORY_CHOICES` убраны.

- [x] **`quizzes/models.py` → `Category`** — поле `name` удалено (миграция `0002_remove_category_name.py`), осталось только `title`.

- [x] **`ai_generator/prompts.py`** — промпт и `_questions_to_initial` переведены на числовой `correct_answer_index` вместо сравнения текста ответа.

## Стоит сделать (не блокирует, но улучшит надёжность)

- [x] **`quizzes/services.py`** — создание `Quiz`+`Question`+`AnswerOption` обёрнуто в `@transaction.atomic`.
- [ ] Нет проверки, что у каждого `Question` создаётся ровно один `AnswerOption` с `is_correct=True` — это инвариант, который сейчас держится только корректностью `correct_index` из формы, БД его не проверяет. Учитывать при написании тестов на `create_quiz_from_any_data`.
- [ ] После `migrate` таблица `Category` пустая — форма генерации не заработает, пока не создать хотя бы одну категорию (через `/admin` или `manage.py shell`).
- [ ] `STATICFILES_DIRS` указывает на несуществующую папку `static/` — создать папку или убрать из настроек (сейчас просто предупреждение, не блокирует).

## Найдено при ревью сессии от 2026-07-27

- [x] **`ai_generator/views.py` → `index()`** — `category=form.cleaned_data['category']` падал с `ValueError`, т.к. `ChoiceField` отдаёт строку с pk, а не инстанс `Category`, а `category=` на `ForeignKey` принимает только инстанс. Исправлено на `category_id=form.cleaned_data['category']` (attname FK-поля принимает голый pk, Django сам приведёт строку к нужному типу при сохранении).
- [x] **`quizzes/services.py` → `create_quiz_from_any_data`** — при переименовании внутреннего индекса цикла опций в `opt_index` (см. пункт выше про `enumerate`) забыли обновить `order = i_index` внутри `AnswerOption.objects.create(...)` — использовался индекс внешнего цикла (вопроса), поэтому все варианты одного вопроса получали одинаковый `order`. Исправлено на `order = opt_index`.
- [ ] **`ai_generator/views.py` → `_questions_to_initial()`** — `int(correct_answer_index)` без обработки ошибок: если Claude не вернёт этот ключ или вернёт нечисловое значение, `index()` упадёт с `TypeError`/`ValueError` вместо вменяемой ошибки пользователю. Old-код имел try/except с молчаливым фолбэком на `0`, но возвращать его не стоит — это ровно тот класс бага (молча показать неверный ответ как правильный), ради ухода от которого делался переход с текста ответа на индекс. Предлагаемое решение: обернуть разбор `res` (весь батч, не по вопросам — раз Claude сломал формат, скорее всего сломан весь JSON) в try/except, залогировать `res` и `gen_request.id` (сырой результат уже сохранён в БД к этому моменту, ничего не теряется) и показать пользователю понятное сообщение об ошибке вместо голого 500.
- [ ] **`ai_generator/views.py`** — импорт `from quizzes.models import Category` больше не используется (единственное использование убрано вместе с блоком фильтрации категории) — мёртвый импорт, можно убрать.
- [ ] **`ai_generator/forms.py`** — пустые классы-заглушки `AnswerOption`/`Question` (строки ~51-55), дублирующие имена моделей из `quizzes`, похожи на мёртвый код — не связаны с текущим функционалом формы.
- [x] **`quizzes/views.py` → `QuizzesDetailView.queryset`** — `.prefetch_related("options")` не существует напрямую на `Quiz` (это related_name у `Question`, не у `Quiz`), падал бы с `FieldError`. Исправлено на `.prefetch_related("questions__options")`.
- [x] **`quizzes/models.py`** — `Question`/`AnswerOption` не имели `Meta.ordering`, хотя есть поле `order` — вопросы/варианты могли рендериться в произвольном порядке. Добавлен `Meta: ordering = ['order']` в обе модели.
- [x] **`ai_generator/views.py` → `save()`** — `reverse("quizzes:quizzes_details pk=quiz.pk")` был синтаксически сломан (весь `kwargs` попадал в имя маршрута, `NoReverseMatch`) и стоял вне `if formset.is_valid():` (риск `UnboundLocalError` на `quiz`, если formset невалиден). Исправлено на `reverse("quizzes:quizzes_details", kwargs={"pk": quiz.pk})` внутри блока `if formset.is_valid():`.
- [ ] **`ai_generator/views.py` → `save()`** — если `formset.is_valid()` — `False`, код проваливается на `return redirect(reverse("ai_generator:index"))` — пользователь молча теряет введённые правки, ошибки формы нигде не показываются. Стоит рендерить `temp_ai_quiz.html` с этим же `formset` (уже содержит `.errors`), а не редиректить на index.
