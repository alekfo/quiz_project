# План: перевод мультиплеера на раундовую архитектуру (QuizSeries/SeriesRun)

Черновой рабочий план для сессии реализации. Не код — чек-лист шагов с указанием
затрагиваемых файлов и того, что именно должно измениться и почему. Порядок шагов
подобран так, чтобы после каждого шага проект оставался в рабочем состоянии
(насколько это возможно) и было проще откатиться/остановиться на середине.

Контекст решения (см. обсуждение в чате перед этим планом):
- `Room.current_series_run` уже существует в модели (миграция `0004_room_current_series_run.py`),
  но меняется его роль (см. следующий пункт) — понадобится новое поле и новая миграция.
- Разделение "что выбрано" vs "что реально идёт" — по аналогии с уже существующей парой
  `current_quiz`/`current_game_session` у старого флоу:
  - `Room.current_series` (**новое поле**, FK на `QuizSeries`) — что хост выбрал, заполняется
    в `room_select_series`, до старта первого раунда. Аналог `current_quiz`.
  - `Room.current_series_run` (уже существует) — конкретный активный прогон (`SeriesRun`),
    создаётся только в `room_start`, в момент старта **первого** раунда серии. Аналог
    `current_game_session`.
  - `Room.current_game_session` — не меняется вообще, продолжает указывать на активную
    `GameSession` (раунд), на нём по-прежнему держится WS-редирект в `RoomConsumer`.
  Смысл разделения: если хост выбрал серию и передумал/сбросил до старта первого раунда —
  `SeriesRun` ещё не создан, нечего чистить/помечать `abandoned`, обычный `current_series = None`.
- Старый флоу выбора одиночного `Quiz` (`RoomQuizForm`, `room_set_quiz`, `room_reset_quiz`) —
  из мультиплеера убираем полностью. Поле `current_quiz` в модели `Room` перестаёт
  использоваться каким-либо мультиплеерным view, но из БД пока не удаляется
  (DB-cleanup отдельным backlog-шагом, см. Шаг 10).
- `_advance_series_run()` (gameplay/views.py) уже mode-agnostic — переиспользуем как есть.
- CSRF-формы нельзя рендерить через WS-консьюмер (`render_to_string` там без `request`) —
  они остаются статичными элементами страницы, переключаются флагами в WS JSON.
- Общий счёт/leaderboard — единая по форме структура для solo и multiplayer
  (список пар `(user, score)`), ветвление по `mode` — в Python, не в шаблоне.

---

## Шаг 0. Подготовка

- [ ] Убедиться, что ветка `rounds_creating` (или новая ветка от неё) чистая, актуальный
      `git status` перед началом.
- [ ] Прогнать существующий соло-флоу вживую в браузере ещё раз (по TODO.md — соло-флоу
      с новой архитектурой ещё не был протестирован сквозным ручным прохождением) —
      если там всплывут баги, их лучше поймать до того, как мультиплеер начнёт зависеть
      от той же `_advance_series_run`.

## Шаг 1. `gameplay/services.py` — общий хелпер прогресса серии

Новый файл (в проекте пока нет `services.py` в `gameplay`, только в `quizzes`).

- [ ] Функция вида `get_series_progress(series_run) -> dict`, возвращающая единый набор
      данных для partial-шаблона независимо от `series_run.mode`:
  - `completed_sessions` — завершённые `GameSession` этого `series_run`;
  - `current_round` — `Quiz`, соответствующий `series_run.current_round_index` (или `None`,
    если серия завершена);
  - `is_completed` — `series_run.status == "completed"`;
  - `leaderboard` — список пар `(user, total_score)`, отсортированный по убыванию `score`:
    - solo: один элемент — `(series_run.created_by, сумма score по всем завершённым session.participants)`;
    - multiplayer: агрегация по всем `completed_sessions` → `participants.all()` → суммировать
      `score` по `user_id`, сортировать.
  - Логика ветвления по `mode` — только внутри этой функции, наружу отдаётся всегда
    одна и та же форма (partial-шаблон не должен знать про `mode`).
- [ ] Продумать N+1: `series_run` должен приходить с уже сделанным `prefetch_related`
      (`game_sessions__participants__user`, `game_sessions__quiz`) от вызывающей стороны —
      функция сама запросы не оптимизирует, это ответственность view/consumer.

## Шаг 2. `gameplay/views.py::solo_room` — перевести на новый хелпер

- [ ] Заменить самодельный подсчёт `total_score = sum(...)` на вызов `get_series_progress()`.
- [ ] Проверить, что `solo_room.html` при этом продолжает работать (шаблон пока не трогаем —
      это отдельный Шаг 8).
- [ ] Ручная проверка: пройти существующий тестовый `SeriesRun` в соло ещё раз, убедиться,
      что цифры совпадают с тем, что было раньше (регрессионная проверка хелпера).

## Шаг 3. Модель `Room` — новое поле `current_series` + миграция

- [ ] `multiplayer/models.py::Room` — добавить `current_series = models.ForeignKey('quizzes.QuizSeries',
      on_delete=models.SET_NULL, null=True, blank=True, default=None, related_name="rooms_selecting")`
      (имя `related_name` — на выбор, `Quiz.rooms` уже занято старым `current_quiz`, так что для
      `QuizSeries` нужно своё уникальное имя).
- [ ] `python manage.py makemigrations multiplayer` — новая миграция только добавляет поле,
      без data-миграции (поле nullable, дефолт `None`, существующие комнаты не пострадают).
- [ ] Проверить на dev-БД `python manage.py migrate` — простое добавление FK-колонки, без рисков
      вроде `InconsistentMigrationHistory` (это актуально только для `AUTH_USER_MODEL`, здесь
      не при чём).
- [ ] `current_series_run` — миграций не требует (поле уже есть), меняется только то, **когда**
      оно заполняется (см. Шаг 4).

## Шаг 4. Формы — `multiplayer/forms.py`

- [ ] Убрать/оставить нетронутым `RoomQuizForm` (можно оставить в файле, но перестать
      использовать во view — или сразу удалить, если уверены, что старый флоу нигде
      больше не задействован; проверить перед удалением через grep по `RoomQuizForm`).
- [ ] Новая форма `RoomSeriesForm` (или `RoomSeriesSelectForm`) — выбор `QuizSeries`,
      не `Quiz`. Поле — `ModelChoiceField` на `QuizSeries`, а не `ModelForm` на `Room`
      напрямую (т.к. `Room.current_series_run` ссылается на `SeriesRun`, а не на
      `QuizSeries` — выбирает пользователь именно серию, `SeriesRun` создаётся во view).
- [ ] queryset для поля — по аналогии с `RoomQuizForm.__init__`: свои `QuizSeries` хоста
      (`user=host`) — решить, включать ли сюда ещё и публичные серии чужих пользователей
      (`status="public"`) — если да, `Q(user=host) | Q(status="public")`. Уточнить у
      пользователя перед реализацией, если неочевидно из контекста.

## Шаг 5. Views — `multiplayer/views.py`

- [ ] `room_select_series` (замена `room_set_quiz`, `@login_required @require_POST`):
  - только хост (`room.host != user` → `PermissionDenied`, как в остальных host-only view);
  - валидация `RoomSeriesForm`;
  - **никакого `SeriesRun` здесь не создаём** — только `room.current_series = form.cleaned_data["series"]`,
    сбросить `room.room_players.update(is_ready=False)`, `transaction.on_commit(lambda: _notify_room(room))`.
    Простая замена `current_quiz` на `current_series`, без побочных созданий объектов.
- [ ] `room_reset_series` (замена `room_reset_quiz`): `room.current_series = None`, сбросить
      `is_ready`, `_notify_room` через `on_commit`. Раз `SeriesRun` на этом этапе ещё не
      существует — нечего чистить/помечать `abandoned`, вопрос из прошлой версии плана снят.
      Отдельно решить: должен ли `room_reset_series` также обнулять `room.current_series_run`,
      если он уже был создан (т.е. хост "сбрасывает" уже частично пройденную серию, а не
      только выбор) — вероятно да, чтобы можно было начать полностью новую серию načисто;
      если так — обнулять оба поля здесь.
- [ ] `room_start` — переписать источник квиза, с веткой "первый раунд vs продолжение":
  - проверка `room.current_series is None` (вместо `room.current_quiz is None`) →
    "не выбрана серия";
  - если `room.current_series_run is None` (первый раунд этой серии в этой комнате) —
    внутри `transaction.atomic()` создать `SeriesRun(series=room.current_series, mode="multiplayer",
    room=room, created_by=host, current_round_index=first_round.round_order if first_round else None)`
    (`first_round = room.current_series.rounds.order_by("round_order").first()`), записать
    в `room.current_series_run`; учесть `IntegrityError` от
    `unique_in_progress_session_run_per_user_series` (`series`+`created_by` при `status="in_progress"`) —
    конфликт возможен, если тот же хост уже гоняет эту же серию где-то ещё (например, в соло) —
    решить, брать существующий `SeriesRun` или отдавать `messages.error` с понятным текстом;
  - если `room.current_series_run` уже есть — доп. проверка `room.current_series_run.status == "completed"`
    → ошибка ("серия уже пройдена, выберите новую" — в UI до этой ветки обычно не дойдёт,
    если форма выбора корректно скрывается по `is_completed`, но на бэке проверка нужна
    в любом случае, раз это POST-эндпоинт);
  - в обоих случаях далее: `curr_quiz = room.current_series_run.series.rounds.filter(round_order=room.current_series_run.current_round_index).first()`,
    `GameSession.objects.create(..., series_run=room.current_series_run)`, как и раньше
    выставить `room.current_game_session = session`, `room.status = "in_progress"`;
  - `IntegrityError`-ветка (`GameSession.objects.get(quiz=..., created_by=user, status="in_progress")`)
    — проверить, не нужно ли туда тоже добавить фильтр по `series_run`.
- [ ] `_get_room_context()` — расширить: добавить в контекст `series_progress` через
      `gameplay.services.get_series_progress(room.current_series_run)` (только если
      `current_series_run` не `None` — на этапе "серия выбрана, но ещё не стартовала"
      прогресса ещё нет), плюс флаги для WS-payload:
      - `has_selected_series` — `room.current_series is not None` (показывать
        кнопку "сбросить"/скрывать форму выбора);
      - `can_start_round` — host, `current_series` есть, (`current_series_run` отсутствует
        ИЛИ не завершена), нет активной `GameSession` в комнате.

## Шаг 6. `multiplayer/consumers.py::RoomConsumer.room_update`

- [ ] `can_confirm` — заменить `bool(room.current_quiz_id)` на `bool(room.current_series_id)`
      (готовность подтверждают к выбранной серии, ещё до того как создан `current_series_run`).
- [ ] Добавить в JSON payload новые флаги из шага 5 (`can_start_round`, `has_selected_series`).
- [ ] `prefetch_related` в начале `room_update` — добавить то, что нужно для
      `get_series_progress` без лишних запросов (`current_series_run__game_sessions__participants__user`,
      `current_series_run__series__rounds`), проверить итоговое число запросов через
      `django-debug-toolbar`/`CONN`-логирование или просто прикинуть по коду.
- [ ] Проверить редирект-ветку (`room.status == "in_progress" and room.current_game_session_id`) —
      она не завязана ни на `current_quiz`, ни на `current_series`, менять не нужно, но
      перепроверить, что она по-прежнему срабатывает после `room_start` с новым источником квиза.

## Шаг 7. `gameplay/views.py::_check_and_make_complete` — продвижение серии в мультиплеере

- [ ] Убрать сброс `sess.room.current_quiz = None` (поле больше не используется этим флоу).
- [ ] НЕ обнулять `sess.room.current_series`/`current_series_run` при завершении отдельного
      раунда — иначе хост не сможет продолжить серию и потеряется возможность показать
      прогресс/лидерборд игрокам. Обнуляются они только явно, через `room_reset_series`
      (Шаг 5), либо когда хост выбирает новую серию поверх завершённой (`room_select_series`
      просто перезапишет `current_series`, а `room_start` для новой серии создаст новый
      `SeriesRun`, если `current_series_run` уже был явно сброшен — см. вопрос в Шаге 5
      про `room_reset_series`).
- [ ] Добавить вызов `_advance_series_run(sess.series_run, completed_round_order=sess.quiz.round_order)`
      — только если `sess.series_run_id` не `None` (на случай, если останутся старые
      `GameSession` без `series_run`, созданные до перехода).
- [ ] Убедиться, что `room.status` по-прежнему переключается на `"waiting"` (это не связано
      с `current_series`/`current_series_run` — комната ждёт следующего хода хоста, будь то
      "начать следующий раунд" или "выбрать новую серию" после завершения текущей).
- [ ] `room.room_players.update(is_ready=False)` — оставить как есть (нужно новое
      подтверждение готовности перед следующим раундом).

## Шаг 8. Шаблоны

- [ ] Новый partial `gameplay/templates/gameplay/_series_progress.html` — вынести туда
      содержимое `<div class="solo-room-main">...</div>` из `solo_room.html`, заменить
      ручной вывод `total_score` на цикл по `series_progress.leaderboard` (пары `(user, score)`),
      убрать хардкод одного игрока.
  - Важно: partial не должен содержать `<form>`/`{% csrf_token %}` — кнопка "начать
    раунд" сюда не входит (остаётся отдельной статичной формой на странице, см. ниже),
    только текстовое отображение прогресса.
- [ ] `solo_room.html` — заинклюдить `_series_progress.html` вместо инлайна, форма
      "начать следующий раунд" остаётся в самом `solo_room.html` (там CSRF не проблема —
      страница рендерится обычным GET/POST, не через WS).
- [ ] `multiplayer/templates/multiplayer/_room_status.html` — заинклюдить туда же
      `_series_progress.html` (передать `series_progress` из контекста), рядом с текущим
      выводом списка участников. Это swap-безопасный read-only фрагмент — обновляется
      у всех подключённых через существующий `outerHTML`-механизм без доп. JS.
- [ ] `multiplayer/templates/multiplayer/room_detail.html`:
  - убрать блок выбора `current_quiz` (`{{quiz_form.current_quiz}}` и связанную форму);
  - добавить статичную форму выбора серии (`RoomSeriesForm`, POST на `room_select_series`,
    видна хосту, когда `has_selected_series` ложно) — с `id`, переключается WS-флагом;
  - добавить статичную кнопку "Сбросить серию" (POST на `room_reset_series`) — видна
    хосту, когда `has_selected_series` истинно;
  - добавить статичную форму "Начать раунд" (POST на `room_start`) — видна хосту, когда
    `can_start_round` истинно (флаг из шага 5/6). Текст кнопки может отличаться для
    "первого раунда" (`current_series_run is None`) и "следующего раунда" — не обязательно,
    но можно прокинуть отдельный флаг/текст, если хочется разной подписи кнопки.
  - расширить существующий `onmessage`-обработчик в `<script>`: добавить переключение
    `hidden` для новых статичных форм по флагам `has_selected_series`/`can_start_round`
    из payload — по образцу того, как уже переключаются `confirmForm`/`readyMsg`.
  - блок "История игр" (`object.game_sessions.all`) — решить, оставлять ли как есть
    (история раундов) или дополнить/заменить историей по `object.series_runs.all()`
    (история прохождений серий целиком) — это отдельное UX-решение, можно отложить
    на потом и сделать в последнюю очередь.

## Шаг 9. Ручное сквозное тестирование в браузере

- [ ] Создать комнату, выбрать серию из нескольких раундов, пригласить второго игрока
      (два разных браузера/профиля или инкогнито).
- [ ] Пройти подряд минимум 2 раунда серии мультиплеером до конца — проверить, что:
  - после завершения раунда обоим игрокам показывается актуальный прогресс без
    перезагрузки страницы;
  - хосту после раунда доступна кнопка "начать следующий раунд", не-хосту — нет;
  - `current_round_index` продвигается корректно (сверить с БД через `manage.py shell`
    или админку);
  - по завершении всей серии обоим видно финальный leaderboard, кнопка "начать раунд"
    скрыта;
  - хост может выбрать **новую** серию в этой же комнате — старый `SeriesRun` остаётся
    доступным как история (`room.series_runs.all()`), не удаляется и не теряется.
- [ ] Проверить edge case: раунд удалён из середины серии между двумя прохождениями
      (совместимость с `_advance_series_run`, который уже устойчив к дыркам в `round_order` —
      но стоит перепроверить именно в мультиплеерной ветке).
- [ ] Проверить edge case: хост нажимает "Сбросить серию" посреди активного раунда
      (если такое вообще технически достижимо через UI) — не должно ломать уже
      идущую `GameSession`.

## Шаг 10. Уборка и документация

- [ ] Grep по всему проекту на использование `current_quiz`/`RoomQuizForm`/`room_set_quiz`/
      `room_reset_quiz` — убедиться, что нигде не осталось мёртвых ссылок (urls.py,
      старые шаблоны, тесты).
- [ ] Обновить `CLAUDE.md` ("Текущее состояние") — зафиксировать переход мультиплеера
      на раундовую архитектуру, как это уже сделано для соло-флоу в записи от 2026-09-13/14.
- [ ] Обновить `TODO.md` — убрать закрытые пункты, отметить, что не протестировано
      живьём vs протестировано.
- [ ] Отдельным пунктом (не в рамках этого перехода, backlog) — решить судьбу поля
      `Room.current_quiz` в БД (теперь точно неиспользуемого мультиплеером): удалять
      миграцией или оставить как "мёртвое" поле на будущее (возврат к быстрой игре
      одним раундом без серии). `Room.current_game_session` — не трогать, используется
      всегда, независимо от источника квиза.
