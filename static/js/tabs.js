// Вкладки: контейнер [data-tabs] с кнопками [role="tab"], у каждой aria-controls="<id панели>".
// Без JS вкладки скрыты (CSS), а все панели видны подряд со своими заголовками.
// Выбранная вкладка запоминается в адресе (#<id панели>), чтобы "Назад" возвращал на неё же.
(function () {
    document.querySelectorAll('[data-tabs]').forEach(function (tablist) {
        const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
        if (!tabs.length) {
            return;
        }

        function panelOf(tab) {
            return document.getElementById(tab.getAttribute('aria-controls'));
        }

        function select(tab, updateHash) {
            tabs.forEach(function (other) {
                const isSelected = other === tab;
                other.setAttribute('aria-selected', isSelected ? 'true' : 'false');
                other.tabIndex = isSelected ? 0 : -1;
                const panel = panelOf(other);
                if (panel) {
                    panel.hidden = !isSelected;
                }
            });
            if (updateHash) {
                history.replaceState(null, '', '#' + tab.getAttribute('aria-controls'));
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
    });
})();
