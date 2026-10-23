# План: перевод подписки и лайков на HTMX

Документ из двух частей:

1. **Теория** — что такое HTMX, как он устроен и как его применять в Django, с короткими примерами.
2. **План под этот проект** — пошаговый перевод `social` (follow/unfollow, like/unlike) на HTMX, с привязкой к конкретным файлам.

Код во второй части — ориентир, а не готовая реализация: пишешь его ты, файлы и имена можно менять.

---

## Часть 1. Теория HTMX

### 1.1. Идея

HTMX — небольшая JS-библиотека (~15 КБ), которая позволяет **любому HTML-элементу** отправлять HTTP-запрос и **подставлять пришедший с сервера HTML** в нужное место страницы. Всё управляется атрибутами `hx-*` прямо в разметке, JS писать не нужно.

Главное отличие от «классического» AJAX (`fetch` + `JsonResponse`):

| | `fetch` + JSON | HTMX |
|---|---|---|
| Что отдаёт сервер | данные (`{"liked": true, "count": 5}`) | готовый кусок HTML |
| Кто строит разметку | JS на клиенте | Django-шаблон на сервере |
| Где логика отображения | дублируется: шаблон для первой отрисовки + JS для обновления | один шаблон, используется в обоих случаях |

Отсюда основной паттерн: **кусок страницы, который должен обновляться, выносится в отдельный partial-шаблон** (`_like_button.html`). Полная страница подключает его через `{% include %}`, а вьюха на HTMX-запрос рендерит только его.

В проекте HTMX уже подключён: `templates/base.html` → `<script src="{% static 'js/htmx.min.js' %}">` (версия 2.0.10). Опыт тоже уже был: поллинг статуса лобби (`hx-get` + `hx-trigger="every 2s"`, сессия от 2026-08-30), потом его заменили на WebSocket.

### 1.2. Минимальный пример

```html
<button hx-post="/clicked/" hx-target="#result" hx-swap="innerHTML">
    Нажми
</button>
<div id="result"></div>
```

По клику HTMX отправит `POST /clicked/`, возьмёт HTML из ответа и вставит его внутрь `#result`. Страница не перезагружается.

### 1.3. Основные атрибуты

**Запрос — какой метод и куда:**

- `hx-get`, `hx-post`, `hx-put`, `hx-patch`, `hx-delete` — URL запроса.
  - Если атрибут стоит на `<form>`, отправляются все поля формы, как при обычном сабмите, **включая `csrfmiddlewaretoken`** из `{% csrf_token %}`.
  - Если на кнопке внутри формы — тоже отправляется вся форма.
  - Если на элементе вне формы — отправляется только он сам (плюс `hx-vals`/`hx-include`).

**Когда отправлять — `hx-trigger`:**

```html
<input name="q" hx-get="/search/" hx-trigger="keyup changed delay:300ms" hx-target="#results">
<div hx-get="/status/" hx-trigger="every 2s"></div>   <!-- поллинг -->
<div hx-get="/lazy/" hx-trigger="load"></div>          <!-- ленивая подгрузка -->
```

Значение по умолчанию зависит от элемента: `submit` у формы, `change` у input/select, `click` у всего остального.

**Куда вставить ответ — `hx-target`:**

- `hx-target="#id"` — CSS-селектор;
- `hx-target="this"` — сам элемент;
- `hx-target="closest .like-block"` — ближайший предок с этим классом;
- `hx-target="find .counter"` — первый потомок с этим классом.

Без `hx-target` ответ подставляется в тот элемент, который отправил запрос.

**Как вставить — `hx-swap`:**

| Значение | Что делает |
|---|---|
| `innerHTML` (по умолчанию) | заменяет содержимое цели |
| `outerHTML` | заменяет цель целиком, включая сам тег |
| `beforeend` / `afterbegin` | дописывает в конец / начало цели (ленты, «загрузить ещё») |
| `delete` | удаляет цель, ответ игнорируется |
| `none` | ничего не вставляет (нужен только побочный эффект запроса) |

Для «кнопки, которая меняет своё состояние» почти всегда нужно **`outerHTML`**. Partial содержит обёртку целиком, и при замене у новой обёртки уже новые атрибуты: другой URL (`like` → `unlike`), другой текст кнопки.

**Удобство и защита от двойных кликов:**

- `hx-disabled-elt="find button"` — на время запроса блокирует кнопку (атрибут `disabled`), повторный клик ничего не отправит;
- `hx-indicator="#spinner"` — на время запроса вешает класс `htmx-request` на индикатор;
- `hx-confirm="Точно удалить?"` — нативный `confirm()` перед запросом;
- `hx-sync="this:drop"` — пока идёт один запрос от элемента, новые отбрасываются.

**Дополнительные данные:**

- `hx-vals='{"source": "preview"}'` — добавить параметры к запросу;
- `hx-include="#other-input"` — прихватить поля из другого места страницы;
- `hx-headers='{"X-CSRFToken": "..."}'` — добавить заголовки.

### 1.4. Заголовки: как сервер узнаёт про HTMX и как им управляет

**HTMX добавляет к каждому запросу свои заголовки:**

- `HX-Request: true` — запрос пришёл от HTMX, а не от обычной навигации браузера (главное);
- `HX-Target` — `id` целевого элемента;
- `HX-Trigger` — `id` элемента, который инициировал запрос;
- `HX-Current-URL` — URL текущей страницы.

В Django:

```python
def is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"
```

Есть пакет `django-htmx`: в нём middleware добавляет `request.htmx`, плюс хелперы для заголовков ответа. Для двух-четырёх вьюх хватит функции выше, пакет можно подключить позже, если HTMX-вьюх станет много.

**Ответ может управлять поведением HTMX через заголовки:**

| Заголовок ответа | Эффект |
|---|---|
| `HX-Redirect: /url/` | полный переход браузера на URL (аналог `redirect()`, но для HTMX-запроса) |
| `HX-Refresh: true` | перезагрузить страницу целиком |
| `HX-Trigger: likeChanged` | вызвать JS-событие на клиенте, другие элементы могут на него подписаться (`hx-trigger="likeChanged from:body"`) |
| `HX-Retarget: #errors` / `HX-Reswap: innerHTML` | сервер переопределяет цель/способ вставки (например, для показа ошибки в другом месте) |

### 1.5. Out-of-band swap: обновить несколько мест одним ответом

Иногда один клик должен обновить два несвязанных места. Например, кнопку лайка и счётчик в шапке. Для этого в ответ добавляется элемент с `hx-swap-oob="true"` и `id`, которые уже есть на странице:

```html
<!-- основной ответ — уйдёт в hx-target -->
<div class="like-block">...</div>

<!-- дополнительный кусок — HTMX найдёт на странице #likes-total и заменит его -->
<span id="likes-total" hx-swap-oob="true">42</span>
```

Для текущей задачи не нужно: счётчик и кнопка лежат в одном partial. Полезно знать на будущее, например для счётчика подписчиков в другом блоке.

### 1.6. Что HTMX делает с ошибками и редиректами (важно)

1. **Ответы 4xx/5xx по умолчанию не вставляются.** Если вьюха ответила `403`/`404`/`500`, HTMX молча ничего не сделает: кнопка «не нажалась». В консоли будет событие `htmx:responseError`. Если нужна обратная связь, её надо сделать явно (см. п. 2.8).
2. **Редирект `302` HTMX проходит прозрачно.** Запрос делает браузерный XHR, а он сам следует редиректам. HTMX получит HTML *конечной* страницы и вставит его в цель. Отсюда классическая ловушка: если сессия истекла, `@login_required` редиректит на `/users/login/`, и **вся страница логина вставляется внутрь кнопки лайка**. Решение — для HTMX-запросов вместо `302` отвечать `HX-Redirect` (см. п. 2.7).
3. **`204 No Content`** — HTMX ничего не вставляет. Удобно для `hx-swap="none"`-сценариев.

### 1.7. CSRF в Django

- HTMX с `hx-post` **на `<form>`** с `{% csrf_token %}` внутри отправляет токен как обычное поле формы. Ничего дополнительно настраивать не надо.
- Если `hx-post` стоит на элементе вне формы (например, на `<button>` сам по себе), токен нужно передать заголовком. Обычно это делается один раз на `<body>`:
  ```html
  <body hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'>
  ```
  После этого все HTMX-запросы на странице несут токен.
- **Partial с формой обязательно рендерить с `request`**: `render(request, "social/_like_button.html", ctx)`. Если рендерить через `render_to_string(...)` без `request`, `{% csrf_token %}` отрисуется пустым. Следующий клик по уже заменённой кнопке тогда упадёт `403 CSRF token missing`. Это тот же класс бага, что уже был в проекте с фрагментом лобби, отрисованным для WebSocket (сессия от 2026-09-02).

### 1.8. Прогрессивное улучшение

Правильный стиль для HTMX — оставлять у формы обычные `method="post"` и `action="..."` и **добавлять** `hx-post` поверх:

```html
<form method="post" action="{% url 'social:create_like' series_id=series.pk %}"
      hx-post="{% url 'social:create_like' series_id=series.pk %}"
      hx-target="closest .like-block" hx-swap="outerHTML">
    {% csrf_token %}
    <button type="submit">Лайкнуть</button>
</form>
```

- Если JS есть, HTMX перехватит сабмит и сделает частичное обновление.
- Если JS нет или HTMX не загрузился, сработает обычная форма с редиректом.

Вьюха при этом обслуживает оба случая: `is_htmx(request)` → partial, иначе → `redirect(...)`.

### 1.9. Кэширование: `Vary: HX-Request`

Если по одному и тому же URL сервер отдаёт то полную страницу, то partial (в зависимости от заголовка `HX-Request`), браузер/прокси может закэшировать не тот вариант. Например, «назад» в браузере покажет голый partial вместо страницы. Защита — заголовок `Vary: HX-Request`:

```python
from django.utils.cache import patch_vary_headers
patch_vary_headers(response, ["HX-Request"])
```

Для наших POST-вьюх это почти неактуально: POST браузер не кэширует. Но это хорошая привычка для будущих GET-эндпоинтов, которые будут отдавать partial.

### 1.10. Отладка

- Вкладка Network в DevTools: у HTMX-запросов виден заголовок `HX-Request: true`, а в Response — пришедший HTML.
- `htmx.logAll()` в консоли браузера — лог всех событий HTMX.
- События для своих обработчиков: `htmx:beforeRequest`, `htmx:afterSwap`, `htmx:responseError`, `htmx:sendError` (сеть недоступна).
- В тестах Django: `client.post(url, HTTP_HX_REQUEST="true")` — эмуляция HTMX-запроса.

---

## Часть 2. План под этот проект: подписки и лайки

### 2.0. Что есть сейчас

| Что | Где | Как работает |
|---|---|---|
| follow / unfollow | `social/views.py::follow`/`unfollow`, `@login_required` + `@require_POST` | `get_or_create` / `filter().delete()`, затем `redirect("users:user_detail")` |
| like / unlike | `social/views.py::create_like`/`delete_like`, то же | серия через `QuizSeries.objects.visible_to(user)`, затем `redirect("quizzes:quizzes_preview")` |
| кнопка подписки | `users/templates/users/user_detail.html` | обычная `<form method="post">`, контекст `followed`/`is_it_me` из `users/views.py::UserDetailView.get_context_data` |
| кнопка лайка | `quizzes/templates/quizzes/quizseries_preview.html` | обычная `<form method="post">`, контекст `nums_of_likes`/`already_liked` из `quizzes/views.py::QuizPreviewView.get_context_data` |

Проблема, которую решаем: каждый клик — это POST, редирект и полная перезагрузка страницы. Позиция скролла теряется. К тому же редирект жёстко ведёт на одну конкретную страницу, поэтому кнопку лайка не получится поставить в другое место (список публичных серий, профиль), не сломав возврат.

### 2.1. Целевая схема

```
[страница]                                   [сервер]
user_detail.html                              social/views.py::follow
  {% include "social/_follow_button.html" %}    get_or_create(...)
     <div class="follow-block">                  if is_htmx(request):
        <form hx-post=".../follow"  ─────────►       render(request, "social/_follow_button.html", ctx)
              hx-target="closest .follow-block"   else:
              hx-swap="outerHTML">                   redirect("users:user_detail", ...)
     </div>  ◄──── новый <div class="follow-block"> с кнопкой «Отписаться»
```

Один partial на каждую кнопку. Он используется и при первой отрисовке страницы (`{% include %}`), и в ответе вьюхи. Контекст для него собирается в одном месте (п. 2.2), чтобы страница и вьюха не разошлись.

### 2.2. Шаг 1 — единый источник контекста для partial'ов: `social/services.py`

Сейчас состояние кнопок считается в двух чужих вьюхах: `UserDetailView` и `QuizPreviewView`. Если partial будет рендериться ещё и из `social/views.py`, логику «подписан ли / сколько лайков» придётся дублировать в третьем месте. Поэтому её стоит вынести:

```python
# social/services.py
from .models import Follow, QuizSeriesLike


def get_follow_state(viewer, target_user) -> dict:
    """Контекст для social/_follow_button.html."""
    return {
        "target_user": target_user,
        "followed": Follow.objects.filter(follower=viewer, following=target_user).exists(),
        "is_it_me": viewer == target_user,
    }


def get_like_state(viewer, series) -> dict:
    """Контекст для social/_like_button.html."""
    return {
        "series": series,
        "nums_of_likes": series.likes.count(),
        "already_liked": series.likes.filter(user=viewer).exists(),
    }
```

Использование:

- в `UserDetailView.get_context_data`: `context.update(get_follow_state(self.request.user, self.object))`;
- в `QuizPreviewView.get_context_data`: `context.update(get_like_state(self.request.user, self.object))`;
- в `social/views.py`: для ответа HTMX-запросу.

> Направление зависимостей: `quizzes.views` и `users.views` импортируют из `social`, а `social` импортирует модели `quizzes`. Это не циклический импорт: `quizzes.models` про `social` ничего не знает, а импорты `social` лежат во вьюхах. `quizzes.views` уже так делает (`from social.models import QuizSeriesLike`). Если захочется, чтобы `quizzes` вообще не знал про `social`, можно сделать template tag `{% like_button series %}` в `social/templatetags/`. Тогда страница `quizzes` зависит от `social` только на уровне шаблона. Для начала это лишнее.

### 2.3. Шаг 2 — partial'ы

Имена начинаются с `_`: это соглашение проекта для фрагментов (`_room_status.html`, `_series_progress.html`).

**`social/templates/social/_follow_button.html`:**

```html
<div class="follow-block">
  {% if not is_it_me %}
    {% if followed %}
      <p>✅ Вы подписаны</p>
      <form method="post" action="{% url 'social:unfollow' user_id=target_user.pk %}"
            hx-post="{% url 'social:unfollow' user_id=target_user.pk %}"
            hx-target="closest .follow-block"
            hx-swap="outerHTML"
            hx-disabled-elt="find button">
        {% csrf_token %}
        <button class="btn" type="submit">Отписаться</button>
      </form>
    {% else %}
      <form method="post" action="{% url 'social:follow' user_id=target_user.pk %}"
            hx-post="{% url 'social:follow' user_id=target_user.pk %}"
            hx-target="closest .follow-block"
            hx-swap="outerHTML"
            hx-disabled-elt="find button">
        {% csrf_token %}
        <button class="btn" type="submit">Подписаться</button>
      </form>
    {% endif %}
  {% endif %}
</div>
```

**`social/templates/social/_like_button.html`** — по той же схеме: обёртка `<div class="like-block">`, внутри `Лайки: {{ nums_of_likes }}` и одна из двух форм (`create_like` / `delete_like`) в зависимости от `already_liked`.

Ключевые моменты:

- **Обёртка присутствует всегда**, даже если кнопки нет (`is_it_me`). `hx-target="closest .follow-block"` ищет её от формы, а `outerHTML` заменяет её целиком.
- **`closest .класс`, а не `#id`**: не нужны уникальные `id`, и partial сразу готов к тому, что на одной странице будет много таких кнопок (список серий с лайками, список пользователей с подпиской). Если всё же нужен `id` (например, для OOB-обновления), делай его с pk: `id="like-{{ series.pk }}"`.
- **`method`/`action` остаются** — это прогрессивное улучшение (п. 1.8).
- **Переменная `target_user`, а не `user_obj`**: partial не должен зависеть от `context_object_name` конкретной `DetailView`. В `user_detail.html` можно передать явно: `{% include "social/_follow_button.html" with target_user=user_obj %}`. Или положить `target_user` в контекст через `get_follow_state` — он там уже есть. Главное, не называть его `user`: так ты затрёшь `user` из context processor, как уже обсуждалось при выборе `user_obj`.

Страницы после этого:

```html
{# users/templates/users/user_detail.html — вместо текущего блока {% if followed %}...{% endif %} #}
{% include "social/_follow_button.html" %}

{# quizzes/templates/quizzes/quizseries_preview.html — вместо блока «Лайки: ...» + форм #}
{% include "social/_like_button.html" %}
```

### 2.4. Шаг 3 — вьюхи: partial для HTMX, редирект для обычного POST

```python
# social/views.py
from django.shortcuts import render

from .services import get_follow_state, get_like_state


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


@login_required
@require_POST
def follow(request, user_id):
    ...  # как сейчас: get_object_or_404, guard на самоподписку, get_or_create, лог

    if _is_htmx(request):
        return render(request, "social/_follow_button.html", get_follow_state(current_user, target_user))
    return redirect("users:user_detail", pk=user_id)
```

То же для `unfollow`, `create_like`, `delete_like`.

- `render(request, ...)`, а не `render_to_string` без `request` — иначе пропадёт CSRF-токен в новой форме (п. 1.7).
- Состояние после действия **считается заново из БД** (`get_*_state`), а не выводится из `created`/`deleted_count`. Если пользователь лайкнул в соседней вкладке, повторный клик всё равно покажет правильное состояние и актуальное число лайков.
- Редирект для не-HTMX ветки остаётся. Если кнопки появятся на других страницах, для этой ветки можно поддержать `?next=` или `HTTP_REFERER` (через `url_has_allowed_host_and_scheme`, чтобы не получить open redirect). HTMX-ветке это не нужно: она вообще не уходит со страницы.
- `delete_like` — хороший момент закрыть открытый trade-off из `TODO.md`: для удаления своего лайка проверка видимости не нужна. Но для HTMX-ответа нужен объект серии, чтобы собрать `get_like_state`. Варианты: (а) оставить `visible_to` как есть; (б) удалять по `series_id` без проверки видимости, а для ответа брать серию через `visible_to` и, если её уже не видно, возвращать пустую обёртку. Решать тебе, (а) проще.
- Если хочешь, `_is_htmx` можно вынести в общий модуль (например, `quiz_project/htmx.py`) — он понадобится и за пределами `social`.

### 2.5. Шаг 4 — вьюхи-страницы берут контекст из `services`

- `users/views.py::UserDetailView.get_context_data` → `context.update(get_follow_state(self.request.user, self.object))`. Вместо текущих `followed`/`is_it_me` отдаёт и `target_user`. Импорт `Follow` в `users/views.py`, скорее всего, станет не нужен.
- `quizzes/views.py::QuizPreviewView.get_context_data` → `context.update(get_like_state(self.request.user, self.object))`. Вместо `nums_of_likes`/`already_liked`. Импорт `QuizSeriesLike` в `quizzes/views.py` (уже сейчас неиспользуемый) убрать.

### 2.6. Шаг 5 — защита от двойного клика

- На клиенте: `hx-disabled-elt="find button"` уже в partial'ах (п. 2.3).
- На сервере: вьюхи уже идемпотентны (`get_or_create` / `filter().delete()`), так что два быстрых запроса ничего не сломают. Два параллельных `get_or_create` в худшем случае дадут `IntegrityError` на `UniqueConstraint`, но `get_or_create` сам обрабатывает эту гонку и повторяет `get`.

### 2.7. Шаг 6 (желательно) — истёкшая сессия при HTMX-запросе

Проблема из п. 1.6: `@login_required` → `302 /users/login/?next=/social/series/5/like` → HTMX вставит страницу логина внутрь `.like-block`. Воспроизвести просто: открыть превью, разлогиниться в другой вкладке, нажать «Лайкнуть».

Варианты по возрастанию охвата:

1. **Свой декоратор** для HTMX-вьюх вместо `login_required`: если пользователь не залогинен и запрос от HTMX — вернуть `HttpResponse(status=204, headers={"HX-Redirect": f"{settings.LOGIN_URL}?next=..."})`, иначе — стандартное поведение.
2. **Middleware**, который для любого HTMX-запроса превращает ответ-редирект на `LOGIN_URL` в `HX-Redirect`. Одно место на весь проект, будущие HTMX-вьюхи будут защищены автоматически.
3. **Пакет `django-htmx`**: в нём есть хелперы (`HttpResponseClientRedirect`), но сам по себе он эту проблему не решает. Декоратор или middleware всё равно нужны.

Для четырёх вьюх хватит варианта 1. Вариант 2 стоит выбрать, если HTMX-эндпоинтов будет больше.

`next` для `HX-Redirect` лучше брать из `HX-Current-URL` (страница, на которой была кнопка), а не из `request.path`. Иначе после логина пользователя вернёт на `POST`-only URL лайка и он получит 405.

### 2.8. Шаг 7 (желательно) — видимая реакция на ошибку

Сейчас `403` (самоподписка через подделанный запрос) и `404` (серию сделали приватной) при HTMX-запросе молча игнорируются: кнопка просто «не работает». Минимальный вариант — один глобальный обработчик в `templates/base.html`:

```html
<script>
  document.body.addEventListener("htmx:responseError", function (evt) {
      alert("Не удалось выполнить действие (" + evt.detail.xhr.status + "). Обновите страницу.");
  });
</script>
```

Позже `alert` можно заменить на вывод в блок `.messages`.

Про `messages.success(...)`: если когда-нибудь добавишь flash-сообщения в эти вьюхи, при HTMX-ответе они **не покажутся сразу**. Блок `messages` рендерится только в `base.html`, а partial его не содержит. Сообщение «всплывёт» на следующей полной загрузке любой страницы. Для HTMX-ответов лучше показывать обратную связь прямо в partial (например, «✅ Вы подписаны» уже это делает).

### 2.9. Шаг 8 — проверка

**Вживую в браузере** (DevTools → Network, смотреть, что у запроса есть `HX-Request: true`, а ответ — короткий HTML-фрагмент, а не вся страница):

1. Чужой профиль → «Подписаться» → кнопка сменилась на «Отписаться» и «✅ Вы подписаны» **без перезагрузки** (позиция скролла та же) → «Отписаться» → обратно.
2. Несколько кликов подряд по уже заменённой кнопке — каждый работает (проверка, что CSRF-токен в новом partial живой).
3. Свой профиль — кнопки нет, обёртка пустая.
4. Превью чужой публичной серии → «Лайкнуть» → счётчик +1, кнопка «Убрать лайк» → обратно, счётчик −1.
5. Та же серия во второй вкладке: лайкнуть там, потом нажать «Лайкнуть» в первой (со старым состоянием). Ответ должен показать реальное состояние («Убрать лайк», правильное число), а не +2.
6. Отключить JS (DevTools → Settings → Disable JavaScript) → кнопки работают через обычный POST + редирект.
7. Разлогиниться в другой вкладке → нажать кнопку → если сделан шаг 6, переход на логин целой страницей. Если не сделан, увидишь страницу логина внутри блока (ожидаемо, это и есть проблема из п. 2.7).

**Тесты** (`social/tests.py`) — по паре на каждую вьюху:

```python
resp = client.post(url, HTTP_HX_REQUEST="true")
assert resp.status_code == 200
assert "Убрать лайк" in resp.content.decode()
assert "<html" not in resp.content.decode()          # именно partial, не страница

resp = client.post(url)                                 # без заголовка
assert resp.status_code == 302                          # старое поведение сохранилось
```

### 2.10. Порядок работы (чек-лист)

- [ ] `social/services.py` — `get_follow_state`, `get_like_state`.
- [ ] `social/templates/social/_follow_button.html`, `_like_button.html`.
- [ ] `user_detail.html` и `quizseries_preview.html` → `{% include %}` partial'ов; `UserDetailView`/`QuizPreviewView` → контекст из `services`.
- [ ] Проверить, что страницы выглядят и работают как раньше (HTMX ещё не задействован, только рефакторинг на partial'ы). **Это хорошая точка для промежуточного коммита.**
- [ ] `hx-post`/`hx-target`/`hx-swap`/`hx-disabled-elt` в partial'ах + ветка `_is_htmx` в четырёх вьюхах `social/views.py`.
- [ ] Проверка по п. 2.9.
- [ ] (желательно) Истёкшая сессия — п. 2.7.
- [ ] (желательно) Глобальный `htmx:responseError` — п. 2.8.
- [ ] (по желанию) Решение по `delete_like` и видимости — п. 2.4.
- [ ] Уборка: неиспользуемые импорты (`JsonResponse`, `Q` в `social/views.py`; `Q`, `QuizSeriesLike` в `quizzes/views.py`; `Follow` в `users/views.py`, если станет не нужен).
- [ ] Обновить `CLAUDE.md`/`TODO.md`.
