/*
 * Экран игры (gameplay/play.html): полоса времени, выбор варианта, кнопка ведущего, живое обновление по WebSocket,
 * анимация вердикта.
 *
 * Входные данные - data-атрибуты на #play-main (см. шаблон). Состояние экрана целиком отрисовано сервером.
 * Ответ уходит через HTMX: блок #play-main подменяется целиком, страница и WS-соединение остаются - поэтому всё,
 * что лежит внутри блока, заново находится в init() после каждой подмены (ссылки - в объекте ui).
 */
(function () {
    'use strict';

    if (!document.getElementById('play-main')) {
        return;
    }

    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var ui = {};

    function playMain() {
        return document.getElementById('play-main');
    }

    function init(revealed) {
        clearInterval(ui.timerId);
        if (ui.hostSkip) {
            clearTimeout(ui.hostSkip.timeoutId);
        }
        ui = {submitting: false};

        initAnswerForm();
        initTimer();
        initNextForm();
        if (revealed) {
            reveal();
        }
    }

    /* ---------- ответ: выбор варианта, защита от двойной отправки ---------- */

    function selectedOption() {
        return ui.form.querySelector('input[name="chosen_option_id"]:checked');
    }

    function syncSubmitButton() {
        ui.submitButton.disabled = ui.submitting || !selectedOption();
    }

    function sendAnswer() {
        if (ui.form && !ui.submitting) {
            // requestSubmit - с событием submit (его перехватывает HTMX); без выбора сервер засчитает пропуск
            ui.form.requestSubmit();
        }
    }

    function initAnswerForm() {
        ui.form = document.getElementById('answer-form');
        if (!ui.form) {
            return;
        }
        ui.submitButton = document.getElementById('submit_button');
        ui.options = ui.form.querySelectorAll('input[name="chosen_option_id"]');

        ui.form.addEventListener('change', syncSubmitButton);
        ui.form.addEventListener('submit', function (event) {
            if (ui.submitting) {
                event.preventDefault();
                return;
            }
            ui.submitting = true;
            syncSubmitButton();
        });
        // запрос не прошёл (сеть, 5xx) - даём ответить ещё раз; тост об ошибке показывает base.js
        ui.form.addEventListener('htmx:afterRequest', function (event) {
            if (!event.detail.successful && ui.form) {
                ui.submitting = false;
                syncSubmitButton();
            }
        });
        syncSubmitButton();
    }

    // цифры - выбрать вариант, Enter - ответить (если вариант выбран)
    document.addEventListener('keydown', function (event) {
        if (!ui.form || ui.submitting || event.altKey || event.ctrlKey || event.metaKey) {
            return;
        }
        var number = parseInt(event.key, 10);
        if (number >= 1 && number <= ui.options.length) {
            event.preventDefault();
            ui.options[number - 1].checked = true;
            ui.options[number - 1].focus();
            syncSubmitButton();
        } else if (event.key === 'Enter' && selectedOption() && event.target.tagName !== 'BUTTON') {
            event.preventDefault();
            sendAnswer();
        }
    });

    /* ---------- полоса времени ---------- */

    function initTimer() {
        var root = playMain();
        var timer = document.getElementById('timer');
        var remaining = parseInt(root.dataset.remaining, 10);
        var limit = parseInt(root.dataset.timeLimit, 10);
        if (!timer || !ui.form || isNaN(remaining)) {
            return;
        }

        var track = timer.querySelector('.timer-track');
        var fill = timer.querySelector('.timer-fill');
        var secondsLabel = document.getElementById('timer-seconds');
        // остаток посчитан сервером от shown_at - здесь только отсчёт вниз от момента загрузки
        var deadline = Date.now() + remaining * 1000;
        var lastSeconds = null;

        function tick() {
            var left = Math.max(0, deadline - Date.now());
            var share = limit > 0 ? left / (limit * 1000) : 0;
            var seconds = Math.ceil(left / 1000);

            fill.style.width = (share * 100) + '%';
            timer.classList.toggle('is-warning', share <= 0.5 && share > 0.25);
            timer.classList.toggle('is-danger', share <= 0.25);
            // последние секунды - полоса пульсирует (CSS, без него при reduced-motion)
            timer.classList.toggle('is-ending', left > 0 && seconds <= 5);
            if (seconds !== lastSeconds) {
                lastSeconds = seconds;
                secondsLabel.textContent = seconds + ' с';
                track.setAttribute('aria-valuenow', String(seconds));
            }
            if (left <= 0) {
                clearInterval(ui.timerId);
                sendAnswer();
            }
        }

        tick();
        ui.timerId = setInterval(tick, 250);
    }

    /* ---------- «Следующий вопрос» / «Пропустить ожидание» (обычная POST-форма, страница перезагружается) ---------- */

    function initNextForm() {
        var form = document.getElementById('next-form');
        if (!form) {
            return;
        }
        var button = document.getElementById('next-button');
        var advancing = false;
        var hostSkip = null;

        // ведущий мультиплеера: пока ответили не все - подтверждение вторым нажатием (без window.confirm)
        if (form.hasAttribute('data-host')) {
            hostSkip = {
                allAnswered: form.dataset.allAnswered === 'true',
                armed: false,
                timeoutId: null,
            };

            hostSkip.disarm = function () {
                clearTimeout(hostSkip.timeoutId);
                hostSkip.armed = false;
                button.classList.remove('btn-danger');
                button.classList.add('btn-primary');
            };

            hostSkip.render = function () {
                if (hostSkip.allAnswered) {
                    hostSkip.disarm();
                    button.textContent = button.dataset.labelNext;
                } else if (!hostSkip.armed) {
                    var waiting = parseInt(form.dataset.participants, 10) - parseInt(form.dataset.answered, 10);
                    button.textContent = 'Пропустить ожидание (' + waiting + ' не ответили)';
                }
            };

            // данные из WS-сообщения status
            hostSkip.update = function (answered, participants, allAnswered) {
                form.dataset.answered = answered;
                form.dataset.participants = participants;
                hostSkip.allAnswered = allAnswered;
                hostSkip.render();
            };

            hostSkip.render();
            ui.hostSkip = hostSkip;
        }

        form.addEventListener('submit', function (event) {
            if (advancing) {
                event.preventDefault();
                return;
            }
            if (hostSkip && !hostSkip.allAnswered && !hostSkip.armed) {
                event.preventDefault();
                hostSkip.armed = true;
                button.textContent = 'Точно пропустить? Нажмите ещё раз';
                button.classList.remove('btn-primary');
                button.classList.add('btn-danger');
                hostSkip.timeoutId = setTimeout(function () {
                    hostSkip.disarm();
                    hostSkip.render();
                }, 4000);
                return;
            }
            advancing = true;
            button.disabled = true;
        });
    }

    /* ---------- вердикт сразу после ответа: вспышка, «+N» к счёту ---------- */
    /* Только после подмены через HTMX - при F5 на экране вердикта всё стоит спокойно. */

    function reveal() {
        var root = playMain();
        var verdict = root.querySelector('.verdict');
        if (!verdict) {
            return;
        }
        // появление вердикта и факта и вспышка карточки - CSS по этим классам (при reduced-motion их там нет)
        root.classList.add('is-revealed');
        if (verdict.classList.contains('is-correct')) {
            root.classList.add('is-flash-correct');
        } else if (verdict.classList.contains('is-wrong')) {
            root.classList.add('is-flash-wrong');
        }

        var points = parseInt(verdict.dataset.points, 10);
        if (points > 0 && !reduceMotion) {
            animateScore(root.querySelector('.play-score-value'), points);
        }
    }

    // как в score-gain.js: «было» -> выезжает «+N» -> число досчитывает и вспыхивает
    function animateScore(value, points) {
        if (!value) {
            return;
        }
        var total = parseInt(value.textContent, 10);
        if (isNaN(total)) {
            return;
        }
        var start = Math.max(0, total - points);
        value.textContent = start;

        var chip = document.createElement('span');
        chip.className = 'score-gain';
        chip.textContent = '+' + points;
        chip.setAttribute('aria-hidden', 'true');
        value.before(chip);
        chip.addEventListener('animationend', function () {
            chip.remove();
        });

        setTimeout(function () {
            var startedAt = performance.now();
            var duration = 700;

            function step(now) {
                var progress = Math.min((now - startedAt) / duration, 1);
                var eased = 1 - Math.pow(1 - progress, 3);
                value.textContent = Math.round(start + (total - start) * eased);
                if (progress < 1) {
                    requestAnimationFrame(step);
                } else {
                    value.textContent = total;
                    value.classList.add('is-flash');
                }
            }

            requestAnimationFrame(step);
        }, 400);
    }

    /* ---------- HTMX-подмена ответа ---------- */

    // сервер мог увести не на play (раунд закончился, пока шёл ответ) - тогда обычный переход
    document.addEventListener('htmx:beforeSwap', function (event) {
        var url = event.detail.xhr.responseURL;
        if (url && new URL(url).pathname !== window.location.pathname) {
            event.detail.shouldSwap = false;
            window.location.href = url;
        }
    });

    // новый #play-main вставлен - заново находим элементы и показываем вердикт
    document.addEventListener('htmx:load', function (event) {
        if (event.detail.elt.id === 'play-main') {
            init(true);
        }
    });

    init(false);

    /* ---------- WebSocket (только мультиплеер) ---------- */

    var socketPath = playMain().dataset.sessionSocket;
    if (!socketPath) {
        return;
    }

    var protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    var socket = new WebSocket(protocol + '://' + window.location.host + socketPath);
    // при уходе со страницы соединение тоже закрывается - это не обрыв
    var leaving = false;

    window.addEventListener('pagehide', function () {
        leaving = true;
    });

    // Сразу после подключения консьюмер присылает снимок (redirect или status) - так ловятся
    // изменения, случившиеся, пока страница грузилась: обработка та же, что у обычного события.
    socket.addEventListener('message', function (event) {
        var data = JSON.parse(event.data);

        if (data.type === 'redirect') {
            leaving = true;
            window.location.href = data.url;
            return;
        }

        // вопрос переключили, а страница об этом не знает - перезагружаемся
        if (String(data.current_question_id) !== playMain().dataset.questionId) {
            leaving = true;
            window.location.reload();
            return;
        }

        var status = document.getElementById('waiting-info');
        if (status && data.html) {
            status.outerHTML = data.html;
        }
        if (ui.hostSkip) {
            ui.hostSkip.update(data.answered_count, data.participants_count, data.all_answered);
        }
    });

    socket.addEventListener('close', function () {
        if (!leaving) {
            document.getElementById('play-connection').hidden = false;
        }
    });
})();
