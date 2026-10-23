// Вкладки: контейнер [data-tabs] с кнопками [role="tab"], у каждой aria-controls="<id панели>".
// Без JS вкладки скрыты (CSS), а все панели видны подряд со своими заголовками.
// Выбранная вкладка запоминается в адресе (#<id панели>), чтобы "Назад" возвращал на неё же.
//
// Один скрипт на все страницы с вкладками. Подключать в конце {% block body %} без defer.
// Вкладки, пришедшие во фрагменте HTMX (например, users/_user_rows.html после поиска),
// подхватываются на событии htmx:load - заново подключать скрипт во фрагменте не нужно.
(function () {
    'use strict';

    // Уже подключённые вкладки. Не атрибутом в DOM: HTMX сохраняет страницу в историю как HTML,
    // атрибут вернулся бы вместе с копией, а обработчики кликов - нет.
    const ready = new WeakSet();

    function initTabs(tablist) {
        // htmx:load приходит и для всей страницы при загрузке - не вешаем обработчики дважды
        if (ready.has(tablist)) {
            return;
        }
        ready.add(tablist);

        const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
        if (!tabs.length) {
            return;
        }

        function panelOf(tab) {
            return document.getElementById(tab.getAttribute('aria-controls'));
        }

        // Плавное появление панели (.is-entering в main.css). Класс снимаем и ставим заново с принудительной
        // перерисовкой между ними - иначе при быстрых переключениях анимация не запустилась бы повторно.
        function animateIn(panel) {
            panel.classList.remove('is-entering');
            void panel.offsetWidth;
            panel.classList.add('is-entering');
        }

        // updateHash = true - переключил пользователь: запоминаем вкладку в адресе и анимируем панель.
        // При открытии страницы (false) панель показывается сразу.
        function select(tab, updateHash) {
            if (updateHash && tab.getAttribute('aria-selected') === 'true') {
                return;
            }
            tabs.forEach(function (other) {
                const isSelected = other === tab;
                other.setAttribute('aria-selected', isSelected ? 'true' : 'false');
                other.tabIndex = isSelected ? 0 : -1;
                const panel = panelOf(other);
                if (panel) {
                    panel.hidden = !isSelected;
                    if (isSelected && updateHash) {
                        animateIn(panel);
                    } else {
                        panel.classList.remove('is-entering');
                    }
                }
            });
            if (updateHash) {
                history.replaceState(history.state, '', '#' + tab.getAttribute('aria-controls'));
            }
        }

        tabs.forEach(function (tab, index) {
            tab.addEventListener('click', function () {
                select(tab, true);
            });
            tab.addEventListener('keydown', function (event) {
                let next = null;
                if (event.key === 'ArrowRight') {
                    next = tabs[(index + 1) % tabs.length];
                } else if (event.key === 'ArrowLeft') {
                    next = tabs[(index - 1 + tabs.length) % tabs.length];
                }
                if (next) {
                    event.preventDefault();
                    select(next, true);
                    next.focus();
                }
            });
        });

        const fromHash = tabs.find(function (tab) {
            return '#' + tab.getAttribute('aria-controls') === location.hash;
        });
        select(fromHash || tabs[0], false);
    }

    function initWithin(root) {
        if (root.matches && root.matches('[data-tabs]')) {
            initTabs(root);
        }
        root.querySelectorAll('[data-tabs]').forEach(initTabs);
    }

    initWithin(document);

    document.addEventListener('htmx:load', function (event) {
        initWithin(event.detail.elt);
    });
})();