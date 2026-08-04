# TODO

Список найден по ревью сессии от 2026-07-24 (AI-генерация викторины: formset для правки вопросов + сохранение в quizzes). Обновлено по ревью сессии от 2026-07-27 — большая часть блокирующих и важных пунктов исправлена. Обновлено по ревью сессии от 2026-07-29 (коммит `d3d7e21` — login/logout, `login_required`). Обновлено по сессии от 2026-07-31 (`quizzes` views/urls, фиксы `{% url %}`/`LOGIN_REDIRECT_URL`, добавлено планирование базового CSS). Обновлено по сессии от 2026-08-04 (ручное создание квиза — `QuizForm`/`QuestionForm`/`QuestionFormSet`/`QuizzesCreateView`, поле `Quiz.audience`).

## Запланировано: базовые CSS

- [ ] **В проекте пока нет ни одного CSS-файла** — вся вёрстка держится на дефолтных user-agent стилях браузера (block/inline элементы по умолчанию), из-за чего разметка на разных страницах выглядит непредсказуемо (например: форма выхода и `div` с именем пользователя в `base.html` стоят друг под другом, а два `<a>` подряд в `quizzes_menu.html` — в один ряд, просто потому что `<form>`/`<div>` — блочные, а `<a>` — строчный элемент). **Начать стоит с ориентации блоков** — до цвета/шрифтов/деталей сперва определиться с layout-контейнерами: шапка/меню (`base.html` — ссылка "QUIZ_PROJECT", логаут, имя юзера) как строка через flex, основной контент — отдельный блочный контейнер под ним. Только после того, как крупные блоки на странице встанут на свои места, есть смысл заниматься частностями (типографика, цвета, отступы).
- [ ] Связанное: `STATICFILES_DIRS` в `settings.py` указывает на несуществующую папку `static/` (см. пункт ниже, в "Стоит сделать") — эту папку в любом случае придётся создать, когда дойдёт до подключения CSS-файла через `{% static %}`.

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

## Найдено при ревью коммита `d3d7e21` от 2026-07-29 (login/logout, login_required)

- [ ] **`users/views.py` → `RateLimitedLoginView.form_invalid`** — `cache.set(rate_key, ..., timeout=30)` — таймаут **30 секунд**, а не час, хотя комментарий к `_LOGIN_RATE_LIMIT` и докстрока класса говорят "в час"/"в час с одного IP". Из-за `timeout=30` окно блокировки — плавающее 30-секундное (сбрасывается почти сразу после паузы во вводе), а не фиксированный час. Похоже на опечатку (`30` вместо `3600`) — стоит проверить, что имелось в виду, и поправить константу (например, вынести таймаут в именованную переменную рядом с `_LOGIN_RATE_LIMIT`, чтобы не расходились по смыслу).
- [ ] **`users/views.py`** — `_REGISTER_RATE_LIMIT` объявлена, но нигде не используется — во view/urls нет ни формы регистрации, ни соответствующего rate-limit. Либо мёртвый код (убрать), либо задел на будущее (тогда стоит пометить `# TODO: используется в register view` явно).
- [ ] **`users/views.py`** — `from django.shortcuts import render` импортируется дважды (отдельной строкой и второй раз вместе с `redirect`) — дублирующийся импорт, можно убрать первую строку.
- [ ] **`ai_generator/templates/ai_generator/base.html` и `quizzes/templates/quizzes/base.html`** — форма "Выйти" рендерится безусловно, без проверки `{% if user.is_authenticated %}` — на страницах, куда неавторизованный пользователь всё же может попасть (например, до применения `login_required` или на будущих публичных страницах), кнопка выхода будет показываться и разлогиненным. Сейчас не критично, т.к. `index()`/`save()`/`QuizzesDetailView` защищены `login_required`/`LoginRequiredMixin`, но стоит держать в уме при добавлении новых, не защищённых логином, страниц.
- [ ] **`users/urls.py`** — есть только `login`/`logout`, `RateLimitedLoginView` рассчитан на защиту от перебора пароля, но регистрации/восстановления пароля пока нет — юзеров в БД можно завести только через `/admin` или `manage.py shell`. Учитывать при следующей сессии по `users`.

## Найдено при ревью сессии от 2026-07-29 (добавлена регистрация: `RegisterForm`, `register()`, honeypot, confirm-email, privacy-policy/public-offer)

- [ ] **Регистрация НЕ протестирована по-настоящему через реальный браузер end-to-end.** Единственная проверка happy-path была через `manage.py shell` + `Client()` — и она случайно засорила общий `CACHES` (`FileBasedCache` в `/tmp/django_cache_quiz_project`), т.к. тестовый клиент и `manage.py runserver` пишут в один и тот же файловый кэш на диске, и оба резолвят IP в `127.0.0.1`. Из-за этого счётчик `register_attempts_127.0.0.1` был выставлен в `_REGISTER_RATE_LIMIT` ещё до первой реальной попытки пользователя, и она сразу же отклонялась с "Слишком много попыток..." — реальный `RegisterForm` с данными пользователя даже не успевал провалидироваться. Нужно: (1) перепройти регистрацию заново вручную через браузер на чистом кэше, (2) держать в уме на будущее, что `manage.py shell`/тестовый `Client()` и `runserver` шарят один файловый кэш — тестирование rate-limited эндпоинтов через shell может испортить состояние для параллельно запущенного dev-сервера (или наоборот).
- [ ] Тестовый прогон оставил в БД мусорного юзера `testuser3` (создан через `Client()` при проверке happy-path) — не реальный аккаунт, стоит удалить вручную (не удалено автоматически, т.к. не было явного разрешения на удаление записи из БД).
- [ ] **`users/views.py` → `register()`** — счётчик `register_attempts_{ip}` инкрементируется на **каждый** POST, включая успешную регистрацию и просто повторный клик — не только на неудачный, как это сделано в `RateLimitedLoginView.form_invalid`. Асимметрия между двумя лимитерами, стоит унифицировать (либо оба считают только неудачи, либо явно обосновать разницу).
- [ ] **`users/templates/users/public_offer.html`** — `{% url 'users:contact' %}` (строка ~102) и `{% url 'users:settings' %}` (строка ~107) падают с `NoReverseMatch` — таких маршрутов в `users/urls.py` нет. Страница `/users/public-offer/` сейчас гарантированно падает 500-й при любом заходе (в т.ч. по ссылке из `register.html`).
- [ ] **`users/templates/users/privacy_policy.html` и `public_offer.html`** — тексты явно скопированы из другого проекта (упоминания "PrepStats", "вакансии", "интервью", "оценка ответов AI") — не описывают QuizApp. Переписать под фактический продукт перед реальным использованием (сейчас это не блокирует рендеринг, кроме двух битых `{% url %}` выше).
- [x] Honeypot-поле `website` было визуально видно пользователю на странице регистрации — `aria-hidden="true"` скрывает только от screen reader'ов/accessibility tree, не от глаз (визуально браузер всё равно рисует обычный `<input>`). Исправлено: обёрнуто в `style="position:absolute; left:-9999px; top:-9999px;"` (`register.html`), `aria-hidden` оставлен как дополнение для скринридеров.
- [ ] **Админка** — модель `User` не зарегистрирована в `users/admin.py` (сейчас там дефолтная заглушка `# Register your models here.`). Без этого через `/admin` не видно ни `email_confirmed`, ни `is_premium`/`is_subscribed`/`ai_quizzes_generated`/`ai_quizzes_limit_per_day`/`subscription_expires_at` — приходится лезть в `manage.py shell`, чтобы проверить эти поля у юзера. Стоит добавить `UserAdmin`-наследника (на основе `django.contrib.auth.admin.UserAdmin`) с этими полями в `list_display`/`fieldsets`.

## Найдено при сессии от 2026-08-04 (ручное создание квиза: `QuizForm`/`QuestionForm`/`QuestionFormSet`/`QuizzesCreateView`)

- [ ] **`quizzes/forms.py` → `QuestionFormSet`** — `extra=1` и нет JS для динамического добавления форм в браузере. Сейчас пользователь при ручном создании квиза может ввести ровно один вопрос за один сабмит — кнопки "+ добавить вопрос" нет ни в `quiz_form.html`, ни где-либо ещё. Это осознанно отложенный шаг (см. историю ревью), но без него ручное создание квиза с несколькими вопросами не работает. Нужен JS: клонировать `{{ question_formset.empty_form }}`, подставить реальный индекс вместо `__prefix__`, вставить в DOM, инкрементировать `form-TOTAL_FORMS`. Либо временный обходной путь — поднять `extra=N` до фиксированного разумного числа (без JS, но с ограничением на максимум вопросов за раз).
- [ ] Нет проверки инварианта "у каждого `Question` создаётся ровно один `AnswerOption` с `is_correct=True`" — тот же класс проблемы, что уже отмечен для AI-потока (`create_quiz_from_any_data`), теперь дублируется и в `QuizzesCreateView.forms_valid` — держится только корректностью `correct_index` из формы, БД не проверяет ни там, ни там.
- [ ] **`quizzes/views.py`** — `UpdateView`, `DeleteView` импортированы (строка 3), но нигде не используются — ни `QuizzesUpdateView`, ни `QuizzesDeleteView` пока не существуют. Либо мёртвый импорт (убрать), либо явный задел на будущее (тогда стоит пометить комментарием).
- [ ] **`quizzes/views.py` → `QuizzesCreateView.post()`** — комментарий `#не понятно зячем это` над `self.object = None` устарел: смысл строки уже разобран (см. сессию ревью — `SingleObjectMixin`/`get_context_data()` ожидают атрибут `self.object` ещё до сохранения квиза). Стоит заменить на нормальный комментарий или убрать за ненадобностью.
