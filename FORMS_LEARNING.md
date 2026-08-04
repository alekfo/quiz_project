# Django-формы и formset'ы: `register.html` и `quiz_form.html`

Учебный конспект, написанный по вопросам, которые реально возникали при разборе кода этого проекта — не общий туториал по Django, а объяснение конкретно того, что здесь происходит и почему. Рассчитан на то, что фронтенд (DOM API, события, `fetch`, HTML-формы как таковые) уже знаком — акцент на том, что в Django устроено *не так*, как можно было бы ожидать, приходя со стороны фронта.

Две части:
- **Часть I** — обычная `ModelForm` на примере `users/forms.py::RegisterForm` и `register.html`. Актуально на 2026-07-29.
- **Часть II** — formset (форма для *списка* объектов) на примере `quizzes/forms.py::QuestionFormSet` и `quiz_form.html`. Актуально на 2026-08-04.

Если исходный код заметно изменится — стоит перечитать и поправить этот файл, он описывает конкретный код, а не абстрактную теорию.

---

# Часть I. Обычная `ModelForm`: `register.html`

## I.1. Откуда вообще берётся `user` в `form.save()`

```python
class RegisterForm(UserCreationForm):
    ...
```

`RegisterForm` ничего не переопределяет в `save()` — вызывается `save()` от `BaseUserCreationForm` (Django, `django/contrib/auth/forms.py`):

```python
def save(self, commit=True):
    user = super().save(commit=False)                       # ModelForm.save(commit=False) — собирает instance из cleaned_data, но НЕ пишет в БД
    user = self.set_password_and_save(user, commit=commit)   # user.set_password(password1), потом user.save(), если commit=True
    return user
```

Ключевой момент: `form.save()` — это метод `ModelForm`, доступный любой форме, унаследованной от модели. У формы с самого начала (при инициализации) есть пустой `self.instance` (`User()`), привязанный к ней. `save()` берёт из `cleaned_data` значения полей, перечисленных в `Meta.fields`, проставляет их в `self.instance` и сохраняет. Никакого "магического" готового `user` до вызова `save()` не существует — есть только пустой `instance`, который `save()` наполняет и возвращает уже с `pk`.

**Практическое следствие**: `email` в вашей форме попадёт в `user.email` только потому, что `'email'` входит в `Meta.fields`. Если бы вы забыли добавить его туда, поле `email` всё равно рендерилось бы и валидировалось (см. I.3) — но `form.save()` его бы проигнорировал, потому что `ModelForm.save()` берёт из `cleaned_data` только те ключи, что перечислены в `Meta.fields`.

---

## I.2. Зачем нужен `class Meta` и почему `model` переопределён

```python
class Meta:
    model = User
    fields = ('username', 'email', 'password1', 'password2')
```

Без `Meta` вообще не было бы `ModelForm` — `RegisterForm` была бы обычной `forms.Form` с несвязанными полями, и `save()` был бы недоступен.

**Почему `model = User` переопределён, а не унаследован от родителя.** У `BaseUserCreationForm` в самом Django (`django/contrib/auth/forms.py`) `Meta` выглядит так:

```python
class Meta:
    model = User        # это django.contrib.auth.models.User — СТАНДАРТНЫЙ юзер Django!
    fields = ("username",)
```

Файл импортирует `from django.contrib.auth.models import User` — встроенную модель Django, а не кастомную. В этом проекте `AUTH_USER_MODEL = 'users.User'` (settings.py) — своя модель с дополнительными полями (`ai_quizzes_generated`, `is_premium` и т.д.). Если бы `RegisterForm` не переопределила `Meta`, форма пыталась бы создавать инстансы `django.contrib.auth.models.User` — модели, которая даже не подключена как `AUTH_USER_MODEL`, и всё бы сломалось. Поэтому `model = User` в `forms.py` (где `User` — это `from .models import User`, кастомная модель) не декоративная строка, а обязательное переопределение.

**Зачем `fields` расширен** с `("username",)` до четырёх полей — чтобы `email` реально попадал в `user.email` при сохранении (см. I.1), а `password1`/`password2` перечислены для наглядности (сами по себе они уже объявлены явно на родительском классе — см. I.3, для них `fields` не обязателен).

---

## I.3. `Meta.fields` управляет не всеми полями формы — только автогенерируемыми из модели

Важное правило `ModelForm`, которое неочевидно из документации с первого взгляда: **`Meta.fields` решает только то, какие поля *модели* автогенерируются как поля формы.** Любое поле, явно объявленное как атрибут прямо на классе формы, попадает в форму **всегда**, независимо от того, есть оно в `Meta.fields` или нет.

В `RegisterForm` три явно объявленных поля:

| Поле | Есть в `Meta.fields`? | Есть в модели `User`? | Что это значит |
|---|---|---|---|
| `email` | да | да | явное объявление **переопределяет** автогенерацию из модели (виджет, `label`, свой `clean_email`), и `form.save()` запишет его в `user.email` |
| `password1`, `password2` | да | нет (это не модельные поля вообще, `set_password` работает иначе) | объявлены на `BaseUserCreationForm`, `fields` тут скорее для документирования |
| `privacy_policy` | **нет** | нет | рендерится и валидируется как обычное поле формы, но `form.save()` никогда не попытается записать его в модель — там просто нет такой колонки |
| `website` (honeypot) | **нет** | нет | то же самое — участвует в валидации, не участвует в сохранении |

Это стандартный паттерн: "служебное" поле формы, которое существует только ради валидации/логики запроса (согласие с политикой, honeypot), а не для сохранения в модель.

---

## I.4. Honeypot: `website` и что такое `attrs`

```python
website = forms.CharField(required=False, widget=forms.TextInput(attrs={
    'autocomplete': 'off',
    'tabindex': '-1',
}))
```

**Идея honeypot'а**: простые боты парсят HTML-форму и автоматически заполняют все `<input>`. Человек невидимое поле не видит и не трогает. Если оно оказалось заполнено при сабмите — это признак бота, `clean_website` отклоняет форму:

```python
def clean_website(self):
    if self.cleaned_data.get('website'):
        raise forms.ValidationError('Ошибка валидации формы.')
    return ''
```

**`attrs`** — словарь, который Django копирует буквально как HTML-атрибуты в тег `<input>` при рендере виджета. Django их не интерпретирует, это чистая передача в разметку:
- `autocomplete="off"` — браузер не подставит туда что-то из истории автозаполнения (иначе браузер человека мог бы сам что-то вписать, испортив логику honeypot'а).
- `tabindex="-1"` — поле выпадает из последовательности Tab-навигации, человек, идущий по форме клавиатурой, не попадёт на него случайно.

**Важный нюанс, который был найден на практике в этом проекте**: изначально поле пряталось только через `<div aria-hidden="true">{{ form.website }}</div>` в шаблоне. `aria-hidden` — это ARIA-атрибут, он влияет **только на дерево доступности** (accessibility tree): скринридеры и другое assistive tech игнорируют элемент. На **визуальный рендеринг он не влияет вообще** — браузер рисует обычный текстовый инпут как есть. В проекте нет CSS, поэтому поле оказалось реально видно пользователю на странице (баг, зафиксирован и исправлен — см. `TODO.md`, "Найдено при ревью сессии от 2026-07-29"). Исправление:

```html
<div style="position:absolute; left:-9999px; top:-9999px;" aria-hidden="true">
  {{ form.website }}
</div>
```

Сдвиг за пределы экрана прячет поле визуально, а `aria-hidden` остаётся как дополнение для скринридеров. Специально **не** использован `display:none`/`visibility:hidden` — некоторые более продвинутые скрейперы проверяют computed style именно на эти два свойства и намеренно пропускают такие поля, чтобы не спалиться на honeypot; увод координатами за экран они, как правило, не проверяют.

---

## I.5. `clean_<имя_поля>` — как работает и почему можно объявить для любого поля

```python
def clean_website(self):
    ...

def clean_email(self):
    email = self.cleaned_data.get('email')
    domain = email.rsplit('@', 1)[-1].lower() if email and '@' in email else ''
    if domain in _DISPOSABLE_EMAIL_DOMAINS:
        raise forms.ValidationError('Временные почтовые адреса не поддерживаются.')
    if User.objects.filter(email=email).exists():
        raise forms.ValidationError('Пользователь с таким email уже зарегистрирован.')
    return email
```

Это не два специальных метода с зарезервированными именами — Django **динамически** ищет метод `clean_<name>` для *любого* поля формы через `hasattr`. Механизм — в `BaseForm._clean_fields()` (`django/forms/forms.py`), упрощённо:

```python
def _clean_fields(self):
    for name, field in self.fields.items():
        value = field.widget.value_from_datadict(...)
        value = field.clean(value)                  # встроенная валидация типа поля (EmailField проверит формат)
        self.cleaned_data[name] = value
        if hasattr(self, f'clean_{name}'):           # <-- любое имя поля, без хардкода
            value = getattr(self, f'clean_{name}')()
            self.cleaned_data[name] = value
```

То есть можно объявить `clean_<любое_имя_поля_формы>`, и Django вызовет его сам, если имя совпало. Порядок валидации внутри `form.full_clean()`:

1. **`_clean_fields()`** — по каждому полю: сначала встроенная валидация типа (`field.clean(value)`), потом (если есть) `clean_<name>()`. Именно поэтому в `clean_email` уже можно полагаться на то, что `email` прошёл базовую валидацию `EmailField` (формат корректен) — `cleaned_data.get('email')` там гарантированно валидный адрес или `None`.
2. **`_clean_form()`** — вызывает общий `self.clean()`, если он определён (у `RegisterForm` своего нет, но у родителя `BaseUserCreationForm.clean()` есть — сверяет `password1 == password2`). Это для **межполевой** валидации, когда нужно сравнить несколько полей сразу — `clean_<name>` для этого не годится, он видит только своё поле.
3. **`_post_clean()`** — у `BaseUserCreationForm` тут прогоняются валидаторы пароля из `AUTH_PASSWORD_VALIDATORS` (settings) уже после того, как `self.instance` собран — некоторым валидаторам (например, "пароль не должен быть похож на username/email") нужно знать эти поля пользователя.

То, что метод `clean_<name>` вернёт (`return email`, `return ''`), становится финальным значением `cleaned_data[name]` — можно не только валидировать, но и нормализовать значение (в `clean_website` явно возвращается `''` вместо изначально введённого значения — так честнее, чем оставлять "спам"-значение в `cleaned_data`, даже если форма всё равно будет отклонена).

**Куда попадает ошибка несовпадения паролей.** Казалось бы, это "ошибка между двумя полями", но Django явно кладёт её в конкретное поле — `SetPasswordMixin.validate_passwords()` вызывает `self.add_error("password2", error)`. Поэтому `{% if form.password2.errors %}` в шаблоне корректно её покажет, отдельного вывода "общих" ошибок формы для этого случая не нужно. Но если в форму добавится ещё одна кросс-полевая проверка через `raise ValidationError(...)` внутри `clean()` **без** явного `add_error(field, ...)` — она попадёт в `form.non_field_errors()`, а этого блока в `register.html` сейчас нет нигде — такая ошибка молча не отобразится. Стоит держать в уме при добавлении новых проверок.

---

## I.6. Рендеринг полей в шаблоне: `BoundField`

```html
<label for="{{ form.username.id_for_label }}">{{ form.username.label }}</label>
{{ form.username }}
{% if form.username.help_text %}<span>{{ form.username.help_text }}</span>{% endif %}
{% if form.username.errors %}<ul>{% for e in form.username.errors %}<li>{{ e }}</li>{% endfor %}</ul>{% endif %}
```

`form.username` — это не сам `forms.CharField`/`UsernameField`, объявленный в классе формы, а объект `BoundField`: Django оборачивает поле при обращении `form.<name>`, связывая спецификацию поля с конкретными данными конкретного запроса (и с самой формой). У голого `forms.CharField` нет ни `.label`, ни `.errors`, ни рендера — это просто спецификация валидации/виджета, общая для всех запросов. Всё перечисленное ниже — атрибуты именно `BoundField`.

- **`{{ form.username }}`** без указания субатрибута — рендерит сам `<input>` (`BoundField.__str__()` → `field.widget.render(...)`).

- **`id_for_label`** — HTML `id`, который Django автоматически присвоил полю (по умолчанию `id_%s`, т.е. `id_username`, `id_email`, `id_privacy_policy`). Используется, чтобы связать `<label for="...">` с конкретным `<input>` — клик по `<label>` тогда фокусирует/переключает связанный контрол (важно для доступности и для чекбоксов — см. I.7).

- **`.label`** — текст подписи. Берётся из явного `label=` при объявлении поля (`email = forms.EmailField(..., label='Email')` — так и в `forms.py`), либо, если не задан, генерируется Django автоматически из имени поля / `verbose_name` соответствующего поля модели.

- **`.help_text`** — подсказка под полем, из аргумента `help_text=`. У `password1`/`password2` (объявлены на `SetPasswordMixin.create_password_fields()` в самом Django) это динамический текст из `AUTH_PASSWORD_VALIDATORS` (минимальная длина и т.п.) и статичный "Enter the same password...". У `email`/`website`/`privacy_policy` в этой форме `help_text` не задан → пустая строка, поэтому в шаблоне `{% if form.username.help_text %}` — чтобы не рисовать пустой `<span></span>`, когда подсказки нет.

- **`.errors`** — `ErrorList` (список сообщений) для конкретно этого поля. Заполняется только после `form.full_clean()`, который вызывается лениво при обращении к `form.errors`/`form.is_valid()`, и **только если форма bound** (создана с `data=request.POST`). На GET (`RegisterForm()` без данных) `.errors` любого поля — всегда пустой список: валидация вообще не запускается для несвязанной формы. После невалидного POST форма возвращается в шаблон с теми же данными, но теперь `.errors` заполнены — это и создаёт эффект "форма подсветила ошибки".

---

## I.7. JS: как чекбокс включает кнопку

```html
{{ form.privacy_policy }}
<label for="{{ form.privacy_policy.id_for_label }}" class="privacy-policy-label">
  Я ознакомлен(а) с ...
</label>
...
<button type="submit" id="submit-btn" disabled>Зарегистрироваться</button>

<script>
  (function () {
    var cb = document.getElementById('{{ form.privacy_policy.id_for_label }}');
    var btn = document.getElementById('submit-btn');
    btn.disabled = !cb.checked;
    cb.addEventListener('change', function () {
      btn.disabled = !this.checked;
    });
  })();
</script>
```

**Два способа поставить галочку, один результат.** Клик прямо по квадратику чекбокса — браузер сам (без единой строчки JS) переключает встроенное свойство `checkbox.checked`. Клик по тексту `<label for="id_privacy_policy">` — тоже нативное поведение браузера: раз `for` совпадает с `id` реального контрола, клик по любому месту `<label>` браузер трактует как клик по связанному контролу и сам его "нажимает". Оба сценария дополнительно порождают DOM-событие `change` на чекбоксе — тоже встроенное поведение (стреляет при переключении мышью, кликом по label или клавиатурой: Tab + Space).

**IIFE** — `(function () { ... })()` — immediately invoked function expression. Всё внутри (`cb`, `btn`) — локальные переменные, не утекают в `window`, чтобы не столкнуться с другим кодом на странице, если он тоже объявит переменную с именем `cb`/`btn`.

**`addEventListener('change', fn)`** не выполняет `fn` сразу — только регистрирует подписку: "когда на `cb` случится `change` — вызови `fn`". Дальше `fn` молчит, пока пользователь не переключит чекбокс.

**Частое заблуждение, которое стоит явно проговорить: это НЕ цикл.** Может показаться, что `cb.addEventListener('change', function () {...})` — это что-то вроде `while True` в Python, которое постоянно опрашивает `cb.checked` и крутится в фоне. Это не так. Здесь нет полинга (polling) и нет постоянно работающего кода — это событийная модель (event-driven): строка `cb.addEventListener(...)` выполняется **один раз**, при загрузке страницы, и просто кладёт функцию-колбэк в список подписчиков на событие `change` у элемента `cb`. После этого JS-движок вообще не выполняет тело этой функции, пока браузер сам не сгенерирует событие `change` (клик по чекбоксу, клик по связанному `<label>`, или Tab+Space с клавиатуры). Никакого расхода CPU между событиями нет — колбэк просто "лежит" зарегистрированным. Разница принципиальная: цикл — это активное, синхронное, непрерывное выполнение; `addEventListener` — это пассивная, асинхронная, однократная регистрация реакции на будущее событие.

**`this` внутри обработчика** — указывает на элемент, на котором событие произошло (`cb`), потому что `fn` — обычная `function`, а не стрелочная. Если бы было `() => { ... }`, `this` брался бы из внешнего контекста (в данном случае — из области видимости IIFE), а не от чекбокса, и `this.checked` сломался бы.

**`btn.disabled = !this.checked`** — ставит/снимает нативный булев атрибут `disabled`. Браузер сам красит disabled-кнопку по умолчанию, не даёт по ней кликнуть (не порождает `click`, не сабмитит форму) и исключает её из Tab-навигации.

**Уточнение по терминологии, раз речь о начальном состоянии.** Непроверенный чекбокс при загрузке страницы — это не потому, что `privacy_policy = forms.BooleanField(required=True)` где-то хранит "default False": у form-полей (в отличие от модельных) вообще нет параметра `default` — есть `initial`, и здесь он не передан. Чекбокс рендерится непроверенным просто потому, что на GET-запросе форма unbound: нет ни `initial`, ни данных из `request.POST`, значит виджет `CheckboxInput` не получает признак `checked`. `cb.checked` в JS читает состояние конкретного DOM-узла в браузере в момент выполнения скрипта — это НЕ чтение какого-то python-значения формы, а факт о текущем HTML.

**Строка `btn.disabled = !cb.checked;` до `addEventListener`** — выполняется один раз при загрузке страницы, синхронизирует начальное состояние кнопки с состоянием чекбокса *в момент рендера в браузере* (а не в момент, когда Django рендерил HTML на сервере — там чекбокс на GET всегда пуст). Редко заметно на практике (HTML и так рендерится с `disabled` на кнопке и пустым чекбоксом — состояния совпадают), но защищает от краевого случая: если браузер восстановил состояние формы из bfcache при переходе "Назад" (чекбокс уже отмечен), JS-обработчики заново "задним числом" не срабатывают — без этой строки кнопка осталась бы залоченной, хотя чекбокс уже стоит отмеченным.

**Важно**: это чисто UX-удобство, не защита. Реальная гарантия — на бэкенде: `privacy_policy = forms.BooleanField(required=True, ...)`. Если JS отключён или кто-то руками снимет атрибут `disabled` через devtools и отправит форму без галочки — `form.is_valid()` всё равно вернёт `False`, и `form.privacy_policy.errors` покажет `"Необходимо принять политику..."`. JS здесь только чтобы не гонять заведомо невалидный POST на сервер.

---

## Связанные баги/находки — `register.html`

Полный список — в [`TODO.md`](TODO.md), раздел "Найдено при ревью сессии от 2026-07-29". Кратко, что уже нашли по ходу разбора именно этой формы:
- honeypot был визуально виден пользователю (`aria-hidden` не прячет визуально) — **исправлено**;
- `register()` считает rate-limit по **каждому** POST, а не только по неудачному (асимметрия с `RateLimitedLoginView`);
- `public_offer.html` падает `NoReverseMatch` на несуществующих `users:contact`/`users:settings`;
- регистрация ещё не проверена end-to-end через реальный браузер (только через `manage.py shell` + `Client()`, что попутно засорило rate-limit кэш и заблокировало первую реальную попытку пользователя).

---

# Часть II. Formset: список форм на одной странице — `quiz_form.html`

`ModelForm` из части I умеет одно: собрать/провалидировать/сохранить **один** инстанс модели. Но при создании квиза на одной странице нужно сразу несколько `Question` (переменное количество, задаётся пользователем). Для этого в Django есть отдельный механизм — **formset**: не форма, а *менеджер списка одинаковых форм*, который знает, как провалидировать и сохранить их все разом.

## II.1. `QuestionFormSet` — что это и откуда

```python
QuestionFormSet = forms.inlineformset_factory(
    Quiz, Question,
    form=QuestionForm,
    extra=1,
    can_delete=True,
)
```

`inlineformset_factory` — специализация formset для конкретно **родитель → дети через ForeignKey** (`Question.quiz` — FK на `Quiz`). В отличие от обычного `modelformset_factory`, он сам знает, как проставить `question.quiz = <тот_самый_quiz>` каждому сохранённому `Question` — не нужно делать это вручную.

- `extra=1` — сколько *пустых* дополнительных форм показать сверх уже существующих (в `QuizzesCreateView` квиз ещё не создан, существующих `Question` нет вообще, поэтому `extra=1` — это единственная форма, которая изначально отрисуется).
- `can_delete=True` — добавляет к каждой форме дополнительное поле-чекбокс `DELETE` (см. II.8).

Фронтенд-аналогия: `QuestionFormSet` ведёт себя как массив однотипных объектов (`[QuestionForm, QuestionForm, ...]`) с общими методами `is_valid()`/`save()`, которые под капотом вызывают `is_valid()`/`save()` у каждого элемента.

## II.2. Prefix: как в одном `request.POST` различить вопрос №0 и вопрос №1

HTML-форма при сабмите — это плоский список пар `ключ=значение` (`application/x-www-form-urlencoded` или `multipart/form-data`), без вложенности и без понятия "массив" из коробки. Если бы на странице было три формы вопроса с полем `text`, все три `<input name="text">` перезаписывали бы друг друга в `request.POST` — остался бы только последний.

Django решает это тем же способом, каким решили бы вы на фронте, отправляя список объектов через обычную HTML-форму (без JSON) — префиксом-индексом в имени поля, вроде `items[0][name]`, `items[1][name]`. У Django синтаксис через дефис: каждая подформа получает `prefix = f"{formset.prefix}-{i}"`, и все её поля рендерятся как `name="{prefix}-{имя_поля}"`. Реально в этом проекте (проверено через `manage.py shell`):

```
questions-0-text
questions-0-order
questions-0-fact
questions-0-option_1 ... option_4
questions-0-correct_index
questions-0-id       # скрытое поле pk — см. II.5
questions-0-DELETE   # чекбокс — см. II.8
questions-1-text
...
```

`questions` — это не хардкод и не имя модели, а **`related_name`** связи `Question.quiz` (или его дефолт, если `related_name` не задан) — Django берёт его как дефолтный prefix для inline-формсета. Именно поэтому в JS-коде ниже prefix нигде не хардкожен строкой `"questions"` — это деталь реализации модели, которая может измениться.

## II.3. `management_form` — "манифест массива" рядом с самими формами

```django
{{ question_formset.management_form }}
```

рендерит 4 скрытых `<input>`:

```html
<input type="hidden" name="questions-TOTAL_FORMS" value="1" id="id_questions-TOTAL_FORMS">
<input type="hidden" name="questions-INITIAL_FORMS" value="0" id="id_questions-INITIAL_FORMS">
<input type="hidden" name="questions-MIN_NUM_FORMS" value="0" id="id_questions-MIN_NUM_FORMS">
<input type="hidden" name="questions-MAX_NUM_FORMS" value="1000" id="id_questions-MAX_NUM_FORMS">
```

Раз в POST-теле нет нативного понятия "массив", Django не может просто посмотреть на присланные ключи и понять "сколько вопросов пришло" — а `questions-0-...`, `questions-1-...` могли прийти не по порядку, с пропусками, или вообще не прийти, если конкретная форма была пустой. Поэтому количество форм передаётся **отдельно и явно**, тем же способом, что обычно передают длину массива рядом с самим массивом в бинарных протоколах — здесь эта роль у `TOTAL_FORMS`.

`ManagementForm` (`django/forms/formsets.py`) — на самом деле обычная `Form` с этими 4 `IntegerField(widget=HiddenInput)`. Если этих полей нет в POST вообще — Django кидает `ValidationError("ManagementForm data is missing or has been tampered with")`, formset не может даже начать работу. Отсюда практическое следствие: **эти 4 скрытых поля обязательны**, без них `question_formset.save()` в принципе не заработает.

## II.4. Как сервер использует `TOTAL_FORMS`: `total_form_count()` и `forms`

Прочитано напрямую в `django/forms/formsets.py` (`BaseFormSet`):

```python
def total_form_count(self):
    if self.is_bound:
        # DoS-защита: не дать клиенту заставить сервер собрать
        # произвольно много форм, даже если TOTAL_FORMS подделан
        return min(
            self.management_form.cleaned_data[TOTAL_FORM_COUNT], self.absolute_max
        )

@cached_property
def forms(self):
    return [self._construct_form(i, ...) for i in range(self.total_form_count())]
```

Механика буквально такая: **"сколько форм собирать" = значение поля `TOTAL_FORMS` из POST**. Дальше цикл `range(total_form_count())` конструирует ровно столько подформ, у каждой `prefix = "questions-{i}"`, и каждая сама выбирает из общего `request.POST` только свои поля.

**Прямое следствие для клиентского JS**: если в браузере в DOM появилась пятая форма вопроса (`questions-4-...`), но `TOTAL_FORMS` не увеличен — цикл `range(total_form_count())` до `i=4` просто не дойдёт. Данные этой формы физически лежат в `request.POST`, но `_construct_form(4)` никогда не вызывается — форма не создаётся, не валидируется, не сохраняется. Молча игнорируется, без ошибки. Поэтому в JS (см. II.7) инкремент `TOTAL_FORMS` — не "на всякий случай", а строго обязательный шаг.

## II.5. `INITIAL_FORMS`: "уже существующий объект" vs "новый"

Отдельная от `TOTAL_FORMS` величина — `INITIAL_FORMS`. Она определяет, какие индексы формсет считает формами **уже существующих в БД** объектов, а какие — потенциально новыми:

```python
if i >= self.initial_form_count() and i >= self.min_num:
    defaults["empty_permitted"] = True
```

В `QuizzesCreateView.get_context_data()` формсет создаётся так:

```python
context.setdefault("question_formset", QuestionFormSet())
```

— без `instance=`/`queryset=` (квиза с существующими вопросами ещё физически не существует). Из-за этого `INITIAL_FORMS = 0` всегда, и **все** формы (индексы `0..TOTAL_FORMS-1`) относятся к "extra" — получают `empty_permitted=True`. Практический смысл: пустая, нетронутая форма (в т.ч. только что склонированная в браузере и не заполненная) не ломает валидацию всего формсета — Django просто не требует от неё быть валидной, если пользователь её не трогал (`form.has_changed() == False`).

При сохранении (`question_formset.save()`) эта же граница определяет, какой из двух внутренних методов обработает форму:
- `save_existing_objects()` — для `i < INITIAL_FORMS`: обновляет или (если стоит `DELETE`) удаляет уже существующий `Question` из БД.
- `save_new_objects()` — для `i >= INITIAL_FORMS`: если форма изменена (`has_changed()`) и **не** помечена на удаление — создаёт новый `Question`; иначе молча пропускает.

В `QuizzesCreateView` работает только вторая ветка — квиз создаётся с нуля, обновлять пока нечего.

## II.6. `empty_form` и `__prefix__` — заготовка формы для JS

```python
@property
def empty_form(self):
    form_kwargs = {..., "prefix": self.add_prefix("__prefix__"), "empty_permitted": True, ...}
```

`question_formset.empty_form` — это форма, которую Django **никогда не пытается сохранить и не ждёт в POST**. Она существует только как HTML-заготовка: у неё `prefix = "questions-__prefix__"`, поэтому все её `name`/`id` содержат буквальную строку `__prefix__` вместо числа:

```html
<input type="text" name="questions-__prefix__-text" id="id_questions-__prefix__-text">
```

Смысл: взять этот HTML один раз, подставить в него реальный индекс вместо `__prefix__` — и получится валидная форма с правильным `name`/`id`, готовая встать в общий формсет.

## II.7. Реализация в `quiz_form.html`: `<template>` + строковая замена `__prefix__`

```html
<div id="question-list">
    {% for question_form in question_formset %}
        <div class="question-form">
            <p>Вопрос №{{ forloop.counter }}:</p>
            {{ question_form.as_p }}
            <button type="button" class="delete-question-btn">Удалить вопрос</button>
        </div>
    {% endfor %}
</div>

<button type="button" id="add-question-btn">Добавить ещё вопрос</button>

<template id="empty-question-template">
    <div class="question-form">
        <p>Новый вопрос:</p>
        {{ question_formset.empty_form.as_p }}
        <button type="button" class="delete-question-btn">Удалить вопрос</button>
    </div>
</template>
```

`<template>` — стандартный HTML5-тег, не специфичный для Django: его содержимое браузер парсит, но **не** вставляет в живой DOM и не рендерит визуально (в отличие от `display:none`, где элемент по-прежнему часть дерева документа, просто невидим). Это ровно то, что нужно для заготовки — она физически есть в HTML-документе, но никак не мешает разметке и не участвует в сабмите формы.

```js
const questionList = document.getElementById('question-list');
const addBtn = document.getElementById('add-question-btn');
const emptyTemplate = document.getElementById('empty-question-template');
const totalFormsInput = document.querySelector('input[name$="-TOTAL_FORMS"]');

addBtn.addEventListener('click', function () {
    const newFormIndex = parseInt(totalFormsInput.value, 10);

    const newFormHtml = emptyTemplate.innerHTML.replaceAll('__prefix__', newFormIndex);

    const wrapper = document.createElement('div');
    wrapper.innerHTML = newFormHtml.trim();
    const newFormBlock = wrapper.firstElementChild;
    questionList.appendChild(newFormBlock);

    totalFormsInput.value = newFormIndex + 1;

    bindDeleteButton(newFormBlock.querySelector('.delete-question-btn'));
});
```

Несколько моментов, которые могли бы быть неочевидны:

- **Почему `.innerHTML` + `replaceAll`, а не `template.content.cloneNode(true)`.** `cloneNode` — более "правильный" DOM-way способ клонировать `<template>`, но после клонирования пришлось бы вручную обходить все склонированные элементы и переписывать им `name`/`id` по отдельности (там несколько `<input>`, `<label for="...">`, radio-группа). `__prefix__` в Django — это **буквальная подстрока внутри HTML-текста**, а не JS-переменная и не DOM-атрибут с особым смыслом — значит, её можно заменить одним `String.replaceAll` по всему куску HTML *до* того, как он станет DOM-узлами. Здесь это проще и надёжнее, чем точечно бегать по атрибутам.
- **`querySelector('input[name$="-TOTAL_FORMS"]')`** — CSS-селектор с оператором `$=` ("оканчивается на"). Он не завязан на конкретный prefix формсета (`questions-`), который на самом деле определяется `related_name` модели (см. II.2) — если это имя когда-нибудь изменится, JS не придётся трогать.
- **`newFormIndex = parseInt(totalFormsInput.value, 10)`** — текущее значение `TOTAL_FORMS` и есть индекс следующей формы: если форм уже 1 (индекс занят `0`), следующая свободна под индексом `1` (см. II.4 — именно так `total_form_count()`/`range()` считает индексы на сервере, поэтому нумерация в JS обязана совпадать с тем, что ожидает сервер).

## II.8. Удаление вопроса: чекбокс `DELETE` + `display:none`, а не `.remove()`

```js
function bindDeleteButton(button) {
    button.addEventListener('click', function () {
        const formBlock = button.closest('.question-form');
        const deleteCheckbox = formBlock.querySelector('input[name$="-DELETE"]');
        deleteCheckbox.checked = true;
        formBlock.style.display = 'none';
    });
}
```

`can_delete=True` добавляет каждой форме формсета скрытое от глаз, но не от кода, поле-чекбокс `DELETE` (`BooleanField(required=False)`, рендерится как `<input type="checkbox" name="questions-{i}-DELETE">`). На сервере `save_existing_objects()`/`save_new_objects()` (см. II.5) явно проверяют этот чекбокс: если он отмечен — существующий `Question` удаляется из БД, а новый вообще не создаётся.

**Почему не `formBlock.remove()`.** Это была бы самая интуитивная фронтенд-реакция — но она сломала бы инвариант из II.4: Django ожидает индексы `questions-0-`, `questions-1-`, ..., `questions-(TOTAL_FORMS-1)-` **без пропусков**. Если удалить из DOM среднюю форму (скажем, вопрос №1 из трёх), поля `questions-0-...` и `questions-2-...` останутся как есть — индекс `1` "выпадет" из присланных данных, а `TOTAL_FORMS` по-прежнему будет говорить "3". Сервер честно попытается сконструировать форму с `i=1`, не найдёт для неё данных в `request.POST` — и получит форму, у которой все поля пустые, но которая **не** помечена как "extra и не изменена" достаточно однозначно для всех случаев (особенно если бы `INITIAL_FORMS > 0` — это класс багов "переиндексация массива после удаления элемента", узнаваемый и с фронтенда). Прятать (`display:none`) вместо удаления из DOM снимает эту проблему полностью: индексы никогда не меняются и не смещаются, просто конкретная форма при сабмите явно говорит серверу "эту не сохраняй/удали" через сам чекбокс `DELETE` — ровно то, для чего Django этот чекбокс и предусмотрел.

Для только что добавленной (ещё не заполненной) формы это работает так же гладко: `save_new_objects()` в любом случае пропускает форму, если `not form.has_changed()`, а `DELETE` тут просто дополнительная явная гарантия.

## II.9. Как всё это сходится в `QuizzesCreateView.forms_valid`

```python
question_formset.instance = self.object   # привязать формсет к реально сохранённому Quiz — см. CLAUDE.md
question_formset.save()                    # save_existing_objects() + save_new_objects(), см. II.5

for question_form in question_formset.forms:
    if not question_form.has_changed() or question_form.cleaned_data.get("DELETE"):
        continue
    question = question_form.instance      # уже сохранён, с реальным pk — formset.save() его проставил
    ...
    AnswerOption.objects.create(...)
```

`question_formset.save()` создаёт/обновляет/удаляет сами `Question` — но **не** `AnswerOption`: `option_1..4`/`correct_index` не входят в `Meta.fields` формы `QuestionForm` (это не поля модели `Question`, см. I.3 — тот же паттерн "служебных" полей формы), значит `ModelForm.save()`/`formset.save()` их не видят и не сохраняют. Поэтому после `question_formset.save()` идёт ручной цикл, который использует `question_form.cleaned_data` (доступен только после `is_valid()`), пропуская ровно те формы, которые формсет тоже проигнорировал бы — непомеченные-неизменённые и помеченные `DELETE`. Логика фильтрации здесь продублирована (та же, что внутри `save_new_objects()`), потому что для `AnswerOption` нет автоматики — это ручной цикл, а не встроенный метод формсета.

---

## Связанные баги/находки — `quiz_form.html`

Полный список — в [`TODO.md`](TODO.md), раздел "Найдено при сессии от 2026-08-04 (ручное создание квиза...)". Кратко:
- динамическое добавление/удаление вопросов реализовано (описано выше в II.7–II.8) — **но ещё не проверено в реальном браузере**, только через ревью кода и разбор исходников Django;
- инвариант "у каждого `Question` ровно один `AnswerOption` с `is_correct=True`" ничем не проверяется на уровне БД — держится только корректностью `correct_index` из формы (тот же класс проблемы, что и в AI-потоке, `create_quiz_from_any_data`).