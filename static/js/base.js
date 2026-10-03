/*
 * Общий код base.html: тосты (flash-сообщения), ошибки HTMX, бургер разделов.
 * Подключается в <head> без defer: класс .js на <html> должен появиться до отрисовки,
 * иначе на телефоне список разделов мигнёт открытым (без JS он виден всегда).
 */
(function () {
    'use strict';

    document.documentElement.classList.add('js');

    /* ---------- тосты ---------- */

    // Показать сообщение в правом нижнем углу. level: success | error | warning | info.
    // Список .messages лежит в .container-header-messages (туда же пишет room_detail.html).
    function showToast(text, level) {
        var list = document.querySelector('.messages');
        if (!list) {
            var holder = document.querySelector('.container-header-messages');
            if (!holder) {
                return;
            }
            list = document.createElement('ul');
            list.className = 'messages';
            holder.prepend(list);
        }
        var item = document.createElement('li');
        item.className = level || 'info';
        item.textContent = text;
        list.appendChild(item);
        removeLater(item);
    }

    // Исчезновение - CSS-анимация toast-out (main.css, старт на 14.5с, длится 0.5с).
    // После неё элемент убираем из DOM, чтобы невидимый тост не перекрывал клики.
    // Основной путь - событие animationend; таймер - страховка на случай, когда анимация
    // не проигралась (вкладка была в фоне, анимации отключены в системе).
    var TOAST_LIFETIME_MS = 15500;

    function removeLater(item) {
        setTimeout(function () {
            item.remove();
        }, TOAST_LIFETIME_MS);
    }

    document.addEventListener('animationend', function (evt) {
        if (evt.animationName === 'toast-out' && evt.target.closest('.messages')) {
            evt.target.remove();
        }
    });

    // сообщения, отрисованные сервером (django.contrib.messages)
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.messages li').forEach(removeLater);
    });

    /* ---------- ошибки HTMX ---------- */

    // Срабатывает на ответы 4xx/5xx; событие всплывает до document.
    document.addEventListener('htmx:responseError', function (evt) {
        showToast(
            'Не удалось выполнить действие (' + evt.detail.xhr.status + '). Обновите страницу.',
            'error'
        );
    });

    /* ---------- бургер разделов (телефон) ---------- */

    document.addEventListener('DOMContentLoaded', function () {
        var toggle = document.getElementById('nav-toggle');
        var nav = document.getElementById('site-nav');
        if (!toggle || !nav) {
            return;
        }

        function setOpen(open) {
            nav.classList.toggle('is-open', open);
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        }

        toggle.addEventListener('click', function () {
            setOpen(!nav.classList.contains('is-open'));
        });

        document.addEventListener('keydown', function (evt) {
            if (evt.key === 'Escape' && nav.classList.contains('is-open')) {
                setOpen(false);
                toggle.focus();
            }
        });
    });

    window.QuizCrowd = { showToast: showToast };
})();
