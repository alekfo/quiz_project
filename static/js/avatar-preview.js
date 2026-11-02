/*
 * Предпросмотр аватара в форме редактирования профиля (users/user_update_form.html).
 * Выбранный файл сразу показывается в круге .avatar, отметка "очистить" - первая буква ника.
 * Без JS форма работает как обычно, просто новый аватар виден только после сохранения.
 */
(function () {
    'use strict';

    var holder = document.querySelector('.avatar-edit');
    if (!holder) {
        return;
    }
    var preview = holder.querySelector('.avatar');
    var fileInput = holder.querySelector('input[type="file"]');
    var clearBox = holder.querySelector('input[type="checkbox"]');   // есть, только если аватар уже загружен
    if (!preview || !fileInput) {
        return;
    }

    var savedMarkup = preview.innerHTML;
    var objectUrl = null;

    function render() {
        if (objectUrl) {
            URL.revokeObjectURL(objectUrl);
            objectUrl = null;
        }
        var file = fileInput.files && fileInput.files[0];
        if (file && file.type.indexOf('image/') === 0) {
            objectUrl = URL.createObjectURL(file);
            var img = document.createElement('img');
            img.src = objectUrl;
            img.alt = '';
            preview.replaceChildren(img);
        } else if (clearBox && clearBox.checked) {
            preview.textContent = preview.dataset.initial || '';
        } else {
            preview.innerHTML = savedMarkup;
        }
    }

    fileInput.addEventListener('change', function () {
        // Django отклоняет форму, если одновременно выбран файл и отмечено "очистить"
        if (clearBox && fileInput.files.length) {
            clearBox.checked = false;
        }
        render();
    });

    if (clearBox) {
        clearBox.addEventListener('change', function () {
            if (clearBox.checked) {
                fileInput.value = '';
            }
            render();
        });
    }
})();
