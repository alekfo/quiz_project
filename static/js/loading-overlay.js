/*
 * Экран ожидания для долгих форм (AI-генерация): спиннер и сменяющиеся фразы поверх страницы.
 *
 * Разметка: <form data-loading-overlay="<id оверлея>"> и сам оверлей
 * <div class="loading-overlay" id="..." hidden> с фразами <span> внутри .loading-text.
 * При отправке формы оверлей показывается, а кнопки формы блокируются - повторная отправка
 * (второй платный запрос) невозможна.
 *
 * Фразы сменяются CSS-анимацией, а не setInterval: пока браузер ждёт ответа на обычный POST,
 * мобильные браузеры приостанавливают JS-таймеры, и фразы "зависали" бы. Анимация идёт сама.
 */
(function () {
    'use strict';

    var SLOT_SECONDS = 3.2;   // сколько видна одна фраза
    var FADE_SECONDS = 0.4;   // плавное появление и исчезновение
    var STYLE_ID = 'loading-phrase-style';

    function shuffle(items) {
        for (var i = items.length - 1; i > 0; i--) {
            var j = Math.floor(Math.random() * (i + 1));
            var tmp = items[i];
            items[i] = items[j];
            items[j] = tmp;
        }
        return items;
    }

    // Все фразы лежат друг на друге и проигрывают одну и ту же анимацию со сдвигом по времени.
    // Доля "видимой" части цикла зависит от числа фраз, поэтому keyframes собираются здесь.
    function startPhrases(overlay) {
        var box = overlay.querySelector('.loading-text');
        if (!box) {
            return;
        }
        var phrases = shuffle(Array.from(box.querySelectorAll('span')));
        if (!phrases.length) {
            return;
        }

        var duration = phrases.length * SLOT_SECONDS;
        var fadePct = (FADE_SECONDS / duration) * 100;
        var endPct = 100 / phrases.length;

        var style = document.getElementById(STYLE_ID);
        if (!style) {
            style = document.createElement('style');
            style.id = STYLE_ID;
            document.head.appendChild(style);
        }
        style.textContent =
            '@keyframes loading-phrase {' +
            '0% { opacity: 0; }' +
            fadePct.toFixed(3) + '% { opacity: 1; }' +
            (endPct - fadePct).toFixed(3) + '% { opacity: 1; }' +
            endPct.toFixed(3) + '% { opacity: 0; }' +
            '100% { opacity: 0; }' +
            '}';

        phrases.forEach(function (phrase, index) {
            box.appendChild(phrase);   // порядок в DOM = порядок показа
            phrase.style.animationName = 'loading-phrase';
            phrase.style.animationDuration = duration + 's';
            phrase.style.animationDelay = (-index * SLOT_SECONDS) + 's';
        });
    }

    function setBusy(form, overlay, busy) {
        overlay.hidden = !busy;
        form.querySelectorAll('button').forEach(function (button) {
            button.disabled = busy;
        });
        if (busy) {
            form.dataset.submitting = 'true';
        } else {
            delete form.dataset.submitting;
        }
    }

    document.querySelectorAll('form[data-loading-overlay]').forEach(function (form) {
        var overlay = document.getElementById(form.dataset.loadingOverlay);
        if (!overlay) {
            return;
        }

        form.addEventListener('submit', function (event) {
            if (form.dataset.submitting) {
                event.preventDefault();
                return;
            }
            startPhrases(overlay);
            setBusy(form, overlay, true);
        });

        // "Назад" после генерации: браузер достаёт страницу из кэша вместе с открытым оверлеем
        window.addEventListener('pageshow', function (event) {
            if (event.persisted) {
                setBusy(form, overlay, false);
            }
        });
    });
})();
