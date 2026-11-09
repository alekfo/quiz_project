# Тестовый сервер: как устроен и как работает

Актуализировано 2026-11-09 по Compose/CI, SSH-диагностике и подтверждению пользователя, что оба сайта работают. Dev/staging QuizApp — ветка **release**; production-деплой main пока не включён.

Учебный разбор инфраструктуры тестового сервера, на котором одновременно
работают два Django-проекта: PrepMate (prepstats.pro) и quiz_project
(quizcrowd.online).

---

## 1. Общая картина

Один сервер (IP 5.42.125.223), два независимых Docker Compose-проекта:

| Проект | Каталог | Контейнеры | Роль |
|--------|---------|------------|------|
| prepmate | ~/PrepMate | prepmate-nginx-1, prepmate-web-1, prepmate-db-1 | Основной проект + общий входной Nginx |
| quiz_project | ~/quiz_project | quiz-web, quiz_project-quiz-db-1, quiz_project-quiz-redis-1 | Тестовый проект |

Ключевая идея: у quiz_project нет своего контейнера Nginx. Весь внешний
трафик (80/443) принимает prepmate-nginx-1. В его nginx.conf описаны оба
домена, и Nginx различает их по server_name (HTTP Host / TLS SNI).

---

## 2. Сеть Docker: кто кого видит

### 2.1. Сеть проекта prepmate

Docker Compose по умолчанию создаёт сеть <project>_default -> prepmate_default.
В ней: prepmate-nginx-1, prepmate-web-1, prepmate-db-1.

Внутри этой сети контейнеры резолвят друг друга по именам и алиасам.
Для PrepMate в nginx.conf теперь используется proxy_pass
http://prepmate-web-1:8000;. Имя web неоднозначно: его регистрируют
оба приложения. Для QuizApp используется http://quiz-web:8000.

### 2.2. Сети проекта quiz_project

quiz_project/docker-compose.yml определяет две сети:

  networks:
    internal:           # внутренняя сеть проекта quiz_project
    shared_nginx:       # внешняя сеть, созданная НЕ этим проектом
      external: true
      name: prepmate_default

- internal -> quiz_project_internal. В ней: quiz-web, quiz-db, quiz-redis.
- shared_nginx -> это та же самая prepmate_default, к которой подключён
  prepmate-nginx-1.

Контейнер quiz-web подключён к обеим сетям:

  web:
    container_name: quiz-web
    networks:
      - internal
      - shared_nginx

Зачем: quiz-web должен видеть quiz-db и quiz-redis (через internal)
и быть видимым для prepmate-nginx-1 (через shared_nginx). Именно
благодаря второй сети Nginx может обратиться к http://quiz-web:8000.

### 2.3. Почему сервисы БД и Redis переименованы

В quiz_project/docker-compose.yml сервисы называются quiz-db и quiz-redis,
а не db/redis. Причина: в сети prepmate_default уже есть контейнер
prepmate-db-1 с DNS-алиасом db (от сервиса db в compose PrepMate).
Если бы в quiz_project сервис тоже назывался db, Docker DNS внутри
prepmate_default вернул бы два контейнера на имя db, и quiz-web мог
бы подключиться не к той БД (симптом: password authentication failed,
когда quiz-web попадал на prepmate-db-1). Переименование в
quiz-db/quiz-redis устранило коллизию.

Внутри quiz_project_internal сервис quiz-db доступен по имени quiz-db.
Поэтому в .env нового проекта: DB_HOST=quiz-db, REDIS_HOST=quiz-redis.

---

## 3. Volumes: где физически хранятся данные

### 3.1. Именованные volumes проекта quiz_project

В quiz_project/docker-compose.yml:

  volumes:
    postgres_data:
      name: quiz_postgres_data
    static_files:
      name: quiz_static_files
    media_files:
      name: quiz_media_files

Директива name: фиксирует точное имя volume, без префикса проекта.
Без неё Docker создал бы quiz_project_postgres_data и т.д. Фиксированные
имена нужны, чтобы другой compose-проект (prepmate) мог смонтировать
те же volumes.

Физически на хосте (Mountpoint):

| Volume | Mountpoint на хосте | Что внутри |
|--------|---------------------|------------|
| quiz_postgres_data | /var/lib/docker/volumes/quiz_postgres_data/_data | файлы PostgreSQL (кластер БД) |
| quiz_static_files | /var/lib/docker/volumes/quiz_static_files/_data | результат collectstatic (CSS, JS, admin, favicon) |
| quiz_media_files | /var/lib/docker/volumes/quiz_media_files/_data | пользовательские загрузки (аватарки: users/1/avatar/...) |

Посмотреть Mountpoint:

  docker volume inspect quiz_static_files

### 3.2. Как volumes используются в quiz-web

  web:
    volumes:
      - static_files:/app/staticfiles
      - media_files:/app/media

Запись volume:/path означает: взять именованный volume и смонтировать его
в контейнер по пути /path.

- static_files -> /app/staticfiles — это STATIC_ROOT из settings.py
  (BASE_DIR / 'staticfiles'). Туда collectstatic в entrypoint.sh
  складывает статику.
- media_files -> /app/media — это MEDIA_ROOT (BASE_DIR / 'media').
  Туда Django сохраняет загруженные файлы (аватарки).

/app — это WORKDIR из Dockerfile (WORKDIR /app), то есть корень
проекта внутри контейнера, где лежит manage.py. Папки staticfiles и
media создаются как точки монтирования — Docker создаёт их
автоматически, если их нет, и подкладывает под них volume. Поэтому в
ls -l они видны на одном уровне с manage.py, но это не настоящие
папки образа, а смонтированные volumes.

### 3.3. Как те же volumes используются в prepmate-nginx-1

В prepmate/docker-compose.yml сервис nginx монтирует четыре volume:

    volumes:
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro   # bind-mount конфига
      - static_files:/static                                    # статика PrepMate
      - media_files:/media                                      # media PrepMate
      - quiz_static_files:/static-quiz                          # статика quiz_project
      - quiz_media_files:/media-quiz                            # media quiz_project
      - /etc/letsencrypt:/etc/letsencrypt:ro                    # сертификаты
      - /var/www/certbot:/var/www/certbot:ro                    # ACME challenge

- prepmate_static_files и prepmate_media_files — volumes проекта
  prepmate (с префиксом, т.к. в его compose нет name:). Монтируются в
  /static и /media — их использует блок prepstats.pro.
- quiz_static_files -> /static-quiz, quiz_media_files -> /media-quiz —
  это те же самые volumes, что пишет quiz-web. Благодаря этому Nginx
  видит файлы, которые создал quiz-web.

### 3.4. Один файл — много представлений

  Хост: /var/lib/docker/volumes/quiz_media_files/_data/users/1/avatar/8acf...jpg
    |
    +-> контейнер quiz-web:        /app/media/users/1/avatar/8acf...jpg
    |     (volume media_files:/app/media)
    |
    +-> контейнер prepmate-nginx-1: /media-quiz/users/1/avatar/8acf...jpg
          (volume quiz_media_files:/media-quiz)

Один физический файл, несколько mount point в разных контейнерах. Файл
занимает место на диске ровно один раз — в
/var/lib/docker/volumes/quiz_media_files/_data/... Внутри контейнера это
не копия и не symlink, а тот же самый inode, видимый через mount namespace
контейнера (Docker использует bind-mount/volume mount).

Проверить:

  docker exec quiz-web ls /app/media/users/1/avatar/
  docker exec prepmate-nginx-1 ls /media-quiz/users/1/avatar/
  sudo ls -l /var/lib/docker/volumes/quiz_media_files/_data/users/1/avatar/

Все три команды покажут один и тот же файл.

### 3.5. static/ vs staticfiles/

В контейнере quiz-web видны две папки:

- static/ — исходная статика из репозитория (STATICFILES_DIRS =
  [BASE_DIR / 'static']). Копируется в образ при COPY . . в Dockerfile.
  Это часть кода.
- staticfiles/ — результат collectstatic (STATIC_ROOT), куда
  Django собирает статику из всех приложений + static/. Это volume
  quiz_static_files. Именно её отдаёт Nginx через /static-quiz.

То есть static/ — сырьё в образе, staticfiles/ — готовое в volume.
Nginx смотрит только в staticfiles/ (через /static-quiz).

---

## 4. Полный цикл запроса

### Сценарий A: пользователь открывает https://quizcrowd.online/quizzes/menu/

#### Шаг 1. DNS и TCP/TLS

1. Браузер резолвит quizcrowd.online -> 5.42.125.223 (A-запись).
2. TCP-соединение на 5.42.125.223:443.
3. TLS-рукопожатие: SNI quizcrowd.online. Nginx выбирает сертификат из
   /etc/letsencrypt/live/quizcrowd.online/ (благодаря
   server_name quizcrowd.online в блоке listen 443 ssl).

#### Шаг 2. Nginx выбирает server-блок

Nginx смотрит на Host / SNI и находит блок:

  server {
      listen 443 ssl;
      server_name quizcrowd.online;
      ...
  }

Он не попадает в default_server (тот, что return 444), потому что
server_name совпал.

#### Шаг 3. Nginx выбирает location

URI — /quizzes/menu/. Nginx перебирает location в блоке
quizcrowd.online:

- location /static/ — не подходит.
- location /ws/ — не подходит.
- location /media/ — не подходит.
- location / — подходит (префикс /).

Попадает в:

  location / {
      proxy_pass http://quiz-web:8000;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      ...
  }

#### Шаг 4. Nginx резолвит quiz-web и проксирует

1. Nginx (в контейнере prepmate-nginx-1) обращается к DNS Docker
   127.0.0.11 за именем quiz-web.
2. Поскольку prepmate-nginx-1 и quiz-web оба подключены к сети
   prepmate_default (через shared_nginx), DNS возвращает IP контейнера
   quiz-web (например, 172.18.0.5).
3. Nginx открывает TCP-соединение quiz-web:8000 и отправляет HTTP-запрос:

  GET /quizzes/menu/ HTTP/1.1
  Host: quizcrowd.online
  X-Real-IP: <IP пользователя>
  X-Forwarded-For: <IP пользователя>
  X-Forwarded-Proto: https
  ...

Важно: Host остаётся quizcrowd.online (благодаря
proxy_set_header Host $host;), а X-Forwarded-Proto: https говорит
Django, что снаружи был HTTPS (хотя внутри Nginx->Daphne соединение по HTTP).

#### Шаг 5. Daphne принимает запрос

В контейнере quiz-web работает daphne (ASGI-сервер), запущенный из
entrypoint.sh:

  exec daphne -b 0.0.0.0 -p 8000 quiz_project.asgi:application

Daphne слушает 0.0.0.0:8000. Он принимает HTTP-запрос и передаёт его в
ASGI-приложение quiz_project.asgi:application.

#### Шаг 6. Django/ASGI обрабатывает запрос

asgi.py:

  application = ProtocolTypeRouter({
      'http': django_asgi_app,
      'websocket': AuthMiddlewareStack(...),
  })

Для HTTP-запроса используется django_asgi_app — стандартный Django ASGI
handler. Дальше:

1. Middleware (по порядку из settings.py): SecurityMiddleware,
   SessionMiddleware, CommonMiddleware, CsrfViewMiddleware,
   AuthenticationMiddleware и т.д.
2. URL routing: quiz_project/urls.py -> находит view для /quizzes/menu/.
3. View обращается к БД (quiz-db) через psycopg2, используя
   DB_HOST=quiz-db (внутри сети quiz_project_internal).
4. Шаблон рендерится, в HTML попадают ссылки на статику (/static/...)
   и медиа (/media/...).
5. Response возвращается в Daphne -> Nginx.

#### Шаг 7. Nginx возвращает ответ браузеру

Nginx получает от Daphne HTTP/1.1 200 OK с HTML и отправляет его браузеру
по TLS. Браузер рендерит страницу.

#### Шаг 8. Браузер запрашивает статику и медиа

В HTML есть, например:

  <link rel="stylesheet" href="/static/css/main.css">
  <img src="/media/users/1/avatar/8acf...jpg">

Браузер делает отдельные запросы на
https://quizcrowd.online/static/css/main.css и
https://quizcrowd.online/media/users/1/avatar/8acf...jpg. Для них цикл
повторяется до шага 3, но location выбирается другой:

- /static/css/main.css -> location /static/ -> alias /static-quiz/ ->
  Nginx отдаёт файл напрямую с диска (без участия Daphne). Файл лежит в
  volume quiz_static_files, смонтированном как /static-quiz.
- /media/users/1/avatar/8acf...jpg -> location /media/ ->
  alias /media-quiz/ -> Nginx отдаёт файл напрямую из volume
  quiz_media_files.

Ключевое: статика и медиа не проходят через Daphne — их отдаёт
Nginx, читая файлы из volume. Это быстро и не нагружает Python-процесс.

### Сценарий B: WebSocket (например, multiplayer)

Если пользователь открывает страницу с WebSocket-подключением (например,
комната multiplayer), браузер делает запрос на wss://quizcrowd.online/ws/...

1. TLS-рукопожатие, Nginx выбирает блок quizcrowd.online.
2. URI начинается с /ws/ -> location /ws/.
3. В этом блоке:

  proxy_pass http://quiz-web:8000;
  proxy_http_version 1.1;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "upgrade";
  ...
  proxy_read_timeout 86400;

4. Nginx апгрейдит соединение до WebSocket и проксирует его на Daphne.
5. Daphne (ASGI) направляет WebSocket в AuthMiddlewareStack(URLRouter(...))
   из asgi.py.
6. multiplayer.routing.websocket_urlpatterns +
   gameplay.routing.websocket_urlpatterns определяют consumer.
7. Consumer работает с CHANNEL_LAYERS (Redis, REDIS_HOST=quiz-redis)
   для broadcast между клиентами.
8. proxy_read_timeout 86400 (24 часа) — чтобы Nginx не разрывал
   долгоживущее WebSocket-соединение.

---

## 5. Разбор nginx.conf для quizcrowd.online

  server {
      listen 80;
      server_name quizcrowd.online;
      location /.well-known/acme-challenge/ { root /var/www/certbot; }
      location / { return 301 https://quizcrowd.online$request_uri; }
  }

Порт 80: только для ACME challenge (certbot) и редиректа на HTTPS.
/var/www/certbot — bind-mount с хоста, туда certbot кладёт challenge-файлы.

  server {
      listen 443 ssl;
      server_name quizcrowd.online;
      ssl_certificate     /etc/letsencrypt/live/quizcrowd.online/fullchain.pem;
      ssl_certificate_key /etc/letsencrypt/live/quizcrowd.online/privkey.pem;
      ...
  }

HTTPS-блок. Сертификаты — bind-mount из /etc/letsencrypt хоста (certbot
обновляет их, Nginx читает при старте/reload).

  location /static/ { alias /static-quiz/; }

Запрос /static/css/main.css -> файл /static-quiz/css/main.css внутри
контейнера Nginx. /static-quiz — volume quiz_static_files. alias
заменяет префикс /static/ на /static-quiz/.

  location /media/ { alias /media-quiz/; }

Аналогично для медиа: /media/users/1/avatar/x.jpg ->
/media-quiz/users/1/avatar/x.jpg из volume quiz_media_files.

  location /ws/ {
      proxy_pass http://quiz-web:8000;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "upgrade";
      ...
      proxy_read_timeout 86400;
  }

WebSocket: Upgrade/Connection переводят соединение в WebSocket;
proxy_read_timeout 86400 — долгий таймаут.

  location / {
      proxy_pass http://quiz-web:8000;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      ...
  }

Всё остальное — на Daphne. Host $host сохраняет quizcrowd.online (нужно
для ALLOWED_HOSTS/CSRF). X-Forwarded-Proto $scheme = https (т.к.
запрос пришёл на listen 443 ssl), поэтому Django считает запрос безопасным
(SECURE_PROXY_SSL_HEADER).

### Почему default_server с return 444 не мешает

  server {
      listen 443 ssl default_server;
      server_name _;
      ssl_certificate     /etc/letsencrypt/live/prepstats.pro/fullchain.pem;
      ...
      return 444;
  }

Этот блок срабатывает, только если Host/SNI не совпал ни с одним
server_name. Для quizcrowd.online совпадение есть, поэтому запрос идёт
в нужный блок. return 444 — закрыть соединение без ответа — защита от
сканеров и запросов по IP.

---

## 6. Итоговая схема

  Браузер
    | HTTPS (443), SNI=quizcrowd.online
    v
  prepmate-nginx-1  (порты 80/443, сеть prepmate_default)
    |
    +- /static/*  -> alias /static-quiz/  -> volume quiz_static_files  (отдаёт файл с диска)
    +- /media/*   -> alias /media-quiz/   -> volume quiz_media_files   (отдаёт файл с диска)
    +- /ws/*      -> proxy_pass http://quiz-web:8000  (WebSocket -> Daphne)
    +- /*         -> proxy_pass http://quiz-web:8000  (HTTP -> Daphne)
                          |
                          v
                    quiz-web (Daphne, ASGI)
                          |
                          +- HTTP -> Django (views, ORM)
                          |         |
                          |         v
                          |    quiz-db (PostgreSQL)   [сеть quiz_project_internal]
                          |
                          +- WebSocket -> Channels consumers
                                    |
                                    v
                               quiz-redis (channel layer)

Ключевые идеи:
1. Один Nginx на всё — prepmate-nginx-1 принимает трафик обоих
   доменов и различает их по server_name.
2. Общая сеть prepmate_default — позволяет Nginx видеть quiz-web по
   имени; quiz-web подключён и к своей internal для БД/Redis.
3. Общие volumes — quiz_static_files/quiz_media_files пишет
   quiz-web, читает Nginx. Физически это каталоги в
   /var/lib/docker/volumes/...
4. Статика и медиа не идут через Python — Nginx отдаёт их напрямую из
   volume.
5. Daphne обрабатывает только динамику (HTML, API, WebSocket).

---

## 7. Шпаргалка команд

  # Что где смонтировано в nginx
  docker inspect prepmate-nginx-1 --format '{{range .Mounts}}{{.Name}} -> {{.Destination}}{{"\n"}}{{end}}'

  # Что где смонтировано в quiz-web
  docker inspect quiz-web --format '{{range .Mounts}}{{.Name}} -> {{.Destination}}{{"\n"}}{{end}}'

  # Где физически лежит volume
  docker volume inspect quiz_media_files

  # Файлы видны из обоих контейнеров и с хоста
  docker exec quiz-web ls /app/media/users/1/avatar/
  docker exec prepmate-nginx-1 ls /media-quiz/users/1/avatar/
  sudo ls -l /var/lib/docker/volumes/quiz_media_files/_data/users/1/avatar/

  # Кто в какой сети
  docker network inspect prepmate_default --format '{{range .Containers}}{{.Name}} {{end}}'
  docker network inspect quiz_project_internal --format '{{range .Containers}}{{.Name}} {{end}}'

  # Проверка конфига nginx и перезагрузка без пересоздания
  docker exec prepmate-nginx-1 nginx -t
  docker exec prepmate-nginx-1 nginx -s reload

  # Пересоздать только nginx (изменились mounts или git заменил nginx.conf)
  # Сначала должны работать оба web: prepmate-web-1 и quiz-web.
  cd ~/PrepMate && docker compose up -d --no-deps --force-recreate nginx

---

## 8. Частые грабли (и почему они возникают)

1. host not found in upstream "quiz-web" — Nginx при старте резолвит
   upstream. Если quiz-web ещё не запущен, Nginx падает. Решение: сначала
   запустить quiz_project, потом пересоздать Nginx, либо добавить
   resolver 127.0.0.11 и set $upstream quiz-web; proxy_pass
   http://$upstream:8000; (тогда имя резолвится при каждом запросе).

2. password authentication failed — quiz-web подключился не к той
   БД (коллизия имени db в общей сети). Решение: переименовать сервисы
   БД/Redis в quiz-db/quiz-redis.

3. Статика/медиа не отдаётся (404) — volume не смонтирован в
   prepmate-nginx-1, либо смонтирован под другим именем
   (prepmate_quiz_static_files вместо quiz_static_files). Решение:
   зафиксировать имя через name: в volumes: и пересоздать Nginx.

4. nginx.conf изменился, а Nginx не видит — файл смонтирован как
   bind-mount, git pull заменяет его через rename (новый inode), а
   контейнер продолжает видеть старый. Решение: docker compose up -d
   --force-recreate nginx (не просто reload).

5. .env изменился, а Django не видит — docker compose restart не
   перечитывает .env. Решение: docker compose up -d --force-recreate
   web.

6. Запросы PrepStats через один возвращают 400 — подтверждённая
   2026-11-09 коллизия Docker DNS: оба приложения имеют алиас web
   в prepmate_default. При proxy_pass http://web:8000 nginx получает
   адреса обоих приложений и распределяет запросы между ними. QuizApp
   отклоняет Host: prepstats.pro по ALLOWED_HOSTS. Исправление:
   proxy_pass http://prepmate-web-1:8000 для PrepStats; для QuizApp
   оставить http://quiz-web:8000. Не добавлять чужой домен в ALLOWED_HOSTS.

7. После этого исправления 400 остались — проверяй содержимое файла
   на хосте и в контейнере. Во время диагностики хост уже содержал
   prepmate-web-1, контейнер продолжал видеть web; inode отличались,
   ответы шли 400/200/400/200. Reload успешно перечитывал старый файл.
   Пересоздание только nginx исправило проблему; пользователь подтвердил
   работу обоих сайтов. Обычный reload после изменения IP приложения
   и пересоздание nginx после замены mounted-файла — разные операции.

---

## 9. Что можно улучшить на будущее

- resolver 127.0.0.11 в блоках quizcrowd.online/prepstats.pro —
  чтобы Nginx не падал при старте, если upstream ещё не запущен.
- external: true для quiz_static_files/quiz_media_files в
  prepmate/docker-compose.yml — чтобы убрать WARN про чужой volume.
- Уникальные сетевые алиасы приложений вместо зависимости от
  автоматически сформированного имени prepmate-web-1. Текущие 400
  были вызваны коллизией имени web и старым bind mount, а не
  подтверждённой проблемой keepalive Gunicorn/gevent.
- Вынести общий Nginx в отдельный compose-проект (infra/nginx),
  чтобы он не зависел от PrepMate. Тогда оба проекта будут клиентами
  общего Nginx, и деплой PrepMate не будет влиять на quiz_project.

## 10. SSH, репозитории и действующий CI/CD

Подключение: `ssh prepstats` (проверенный сервер 5.42.125.223).

| Назначение | Локально | На сервере |
|---|---|---|
| QuizApp | `/home/aleksey/PycharmProjects/quiz_project` | `/home/alek_fo/quiz_project` |
| PrepMate / PrepStats | `/home/aleksey/PycharmProjects/PrepMate` | `/home/alek_fo/PrepMate` |
| Общий nginx.conf | `PrepMate/nginx/nginx.conf` | `/home/alek_fo/PrepMate/nginx/nginx.conf` |

QuizApp доступен по https://quizcrowd.online, соседний PrepStats —
https://prepstats.pro. Собственного nginx у QuizApp нет: контейнер
prepmate-nginx-1 принимает 80/443, завершает TLS, отдаёт static/media
обоих проектов и проксирует HTTP/WebSocket QuizApp в Daphne.
Остановка или пересоздание этого nginx кратковременно затрагивает оба сайта.

### QuizApp: release → dev/staging

Источник истины — `.github/workflows/ci.yml` текущего проекта.
Push и pull request в main/release запускают установку зависимостей,
`python manage.py check`, `python manage.py makemigrations --check --dry-run`
и `python manage.py test --verbosity=2`. Тесты используют отдельную БД,
in-memory кэш и channel layer; работающий Redis им не нужен.

Только **push в release**, после успешных тестов, запускает deploy-dev:

```bash
cd /home/alek_fo/quiz_project
git pull origin release
docker compose build --no-cache web
docker compose up -d --force-recreate web
docker exec prepmate-nginx-1 nginx -s reload
docker compose logs web --tail 30
```

Entrypoint ждёт PostgreSQL и Redis, выполняет migrate и collectstatic,
затем запускает `daphne -b 0.0.0.0 -p 8000 quiz_project.asgi:application`.
Эти миграции — часть настроенного деплоя; Codex не запускает migrate
вручную без отдельного поручения пользователя. Prod-job main пока
закомментирован: merge в main не означает production-деплой QuizApp.

### PrepMate: отдельный репозиторий и workflow

Изменения общего nginx делаются локально в репозитории PrepMate и проходят
его CI/CD. На момент проверки release запускает тесты, автоматический
деплой настроен только для push в main. Этот порядок **не совпадает**
с dev-деплоем QuizApp из release. Нельзя коммитить конфиг PrepMate
в репозиторий QuizApp или править серверную копию в обход процесса пользователя.

После доставки изменённого nginx.conf на сервер и запуска обоих приложений:

```bash
cd /home/alek_fo/PrepMate
docker compose up -d --no-deps --force-recreate nginx
docker exec prepmate-nginx-1 nginx -t
docker exec prepmate-nginx-1 sh -c 'grep -n proxy_pass /etc/nginx/conf.d/default.conf'
```

Ожидаются `prepmate-web-1:8000` для PrepStats и `quiz-web:8000` для QuizApp.
После пересоздания дополнительный reload не требуется. Если оба приложения
уже запущены, их, PostgreSQL и Redis перезапускать для этой правки не нужно.

Проверка без внешнего DNS (GET главной, без логина и регистрации):

```bash
curl -sS -o /dev/null -w '%{http_code}\n' --resolve prepstats.pro:443:127.0.0.1 https://prepstats.pro/
curl -sS -o /dev/null -w '%{http_code}\n' --resolve quizcrowd.online:443:127.0.0.1 https://quizcrowd.online/
```

Успешный ответ может быть 200 или редиректом по правилам приложения;
чередования с 400 быть не должно. HTTP-проверка не заменяет проверку WS.

### Переменные окружения и AI

В серверном `.env` QuizApp: DB_HOST=quiz-db, REDIS_HOST=quiz-redis,
REDIS_PORT=6379, домен quizcrowd.online в ALLOWED_HOSTS. Имена volumes:
quiz_postgres_data, quiz_static_files, quiz_media_files; общая сеть nginx —
prepmate_default. Значения паролей и ключей в документацию не копировать.

В текущем локальном коде основной AI-путь — BASE_AI_MODEL=deep_seek
(это default, если переменная не задана), `_ask_deepseek` и DEEPSEEK_API_KEY.
CLAUDE_API_SERVICE_URL/SERVICE_API_KEY нужны только альтернативному
`_ask_claude`; автоматического fallback нет. Переход на DeepSeek на момент
актуализации находится в незакоммиченном рабочем дереве: не считать его
уже установленным на сервере без прохождения CI/CD.

Изменение `.env` требует пересоздания web, простой restart его не перечитывает.
Обычный deploy-dev также пересоздаёт web. Не запускать экспериментальный
`ai_generator/deepseek_prompts.py` для диагностики деплоя: при прямом запуске
он вызывает оба внешних API.

