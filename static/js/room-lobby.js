/*
 * Лобби комнаты (multiplayer/room_detail.html): копирование ссылки и живое обновление по WebSocket.
 *
 * На каждое событие комнаты RoomConsumer.room_update присылает HTML фрагмента _room_status.html
 * и флаги. Во фрагменте - блоки с data-live (статус, квиз, игроки, история): каждый заменяет
 * на странице блок с тем же id. Формы по WS не прислать (render_to_string без request - нет
 * CSRF-токена), поэтому они отрисованы один раз при GET, а здесь только скрываются по флагам.
 */
(function () {
    'use strict';

    var lobby = document.querySelector('.lobby');
    if (!lobby) {
        return;
    }

    /* ---------- ссылка на комнату ---------- */

    var copyButton = document.getElementById('copy-link-btn');
    if (copyButton) {
        copyButton.addEventListener('click', function () {
            var input = document.getElementById('room-link');

            function copied() {
                window.QuizCrowd.showToast('Ссылка скопирована', 'success');
            }

            // clipboard API есть только на https и localhost - иначе старый способ через выделение
            function copyBySelection() {
                input.select();
                if (document.execCommand('copy')) {
                    copied();
                } else {
                    window.QuizCrowd.showToast('Не получилось скопировать — выделите ссылку вручную', 'error');
                }
            }

            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(input.value).then(copied, copyBySelection);
            } else {
                copyBySelection();
            }
        });
    }

    /* ---------- WebSocket ---------- */

    var socketPath = lobby.dataset.lobbySocket;
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

    socket.addEventListener('message', function (event) {
        var data = JSON.parse(event.data);
        if (data.type === 'redirect') {
            leaving = true;
            window.location.href = data.url;
            return;
        }
        if (replaceLiveBlocks(data.html)) {
            applyFlags(data);
        }
    });

    socket.addEventListener('close', function () {
        if (!leaving) {
            document.getElementById('lobby-connection').hidden = false;
        }
    });

    // Возвращает false, если страница перезагружается (комнату закрыли).
    function replaceLiveBlocks(html) {
        var template = document.createElement('template');
        template.innerHTML = html;
        var blocks = template.content.querySelectorAll('[data-live]');

        for (var i = 0; i < blocks.length; i++) {
            var fresh = blocks[i];
            var current = document.getElementById(fresh.id);
            if (!current) {
                continue;
            }
            if (fresh.dataset.roomStatus === 'finished' && current.dataset.roomStatus !== 'finished') {
                leaving = true;
                window.location.reload();
                return false;
            }
            keepOpenDetails(current, fresh);
            current.replaceWith(fresh);
        }
        return true;
    }

    // раскрытая пользователем «История игр» не должна схлопываться от чужого «Готов»
    function keepOpenDetails(current, fresh) {
        current.querySelectorAll('details[id]').forEach(function (details) {
            var twin = fresh.querySelector('#' + details.id);
            if (twin) {
                twin.open = details.open;
            }
        });
    }

    function show(id, visible) {
        var element = document.getElementById(id);
        if (element) {
            element.hidden = !visible;
        }
    }

    function applyFlags(data) {
        // квиз выбран, а блок старта скрыт - значит, идёт раунд
        var roundRunning = data.has_selected_series && !data.start_available;

        show('confirm-ready-form', data.can_confirm);
        show('ready-confirmed-msg', data.is_ready && !roundRunning);
        show('return-to-game', roundRunning);
        show('select-series-form', !data.has_selected_series);
        // квиз выбран, но идёт раунд - менять нельзя (room_reset_series откажет)
        show('reset-series-form', data.start_available);
        show('start-form', data.start_available);

        var startButton = document.getElementById('start-button');
        if (startButton) {
            startButton.disabled = !data.can_start_round;
            startButton.textContent = data.is_first_round ? 'Начать игру' : 'Следующий раунд';
        }

        var startNote = document.getElementById('start-note');
        if (startNote) {
            startNote.hidden = data.can_start_round;
            startNote.textContent = data.players_count
                ? 'Ждём готовности: ' + data.ready_count + ' из ' + data.players_count
                : 'В комнате пока нет игроков';
        }
    }
})();