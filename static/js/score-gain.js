// Анимация прибавки очков в solo_room: result.html ведёт сюда с ?gained=N (очки раунда игрока).
// Счёт показывает «было» (итог - N), рядом выезжает «+N», число досчитывает до итога и вспыхивает.
// Без JS, без параметра и при prefers-reduced-motion - просто итог, как отрендерил сервер.
(function () {
    "use strict";

    const params = new URLSearchParams(window.location.search);
    if (!params.has("gained")) {
        return;
    }
    const gainedRaw = parseInt(params.get("gained"), 10);

    // убираем параметр сразу: F5 и «Назад» не повторят анимацию
    params.delete("gained");
    const query = params.toString();
    window.history.replaceState(null, "", window.location.pathname + (query ? "?" + query : "") + window.location.hash);

    // соло - «Ваш счёт»; мультиплеер - своя строка таблицы участников
    const value = document.querySelector(".score-solo-value")
        || document.querySelector(".score-row[data-me] .score-value");
    if (!value || Number.isNaN(gainedRaw)) {
        return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        return;
    }

    const total = parseInt(value.dataset.score, 10);
    if (Number.isNaN(total)) {
        return;
    }
    // параметр можно подставить руками - держим прибавку в пределах итога
    const gained = Math.min(Math.max(gainedRaw, 0), Math.max(total, 0));
    const start = total - gained;

    const COUNT_DELAY = 600;   // мс: глаз успевает зацепиться за «+N» до начала отсчёта
    const COUNT_DURATION = 1200;

    value.textContent = start;

    const chip = document.createElement("span");
    chip.className = "score-gain" + (gained === 0 ? " is-zero" : "");
    chip.textContent = "+" + gained;
    chip.setAttribute("aria-hidden", "true");
    // в таблице число прижато вправо - метку ставим слева, чтобы число не прыгало, когда она исчезнет
    if (value.classList.contains("score-value")) {
        value.before(chip);
    } else {
        value.after(chip);
    }
    chip.addEventListener("animationend", function () {
        chip.remove();
    });

    if (gained === 0) {
        return;
    }

    window.setTimeout(function () {
        const startedAt = performance.now();

        function step(now) {
            const progress = Math.min((now - startedAt) / COUNT_DURATION, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            value.textContent = Math.round(start + gained * eased);
            if (progress < 1) {
                window.requestAnimationFrame(step);
            } else {
                value.textContent = total;
                value.classList.add("is-flash");
            }
        }

        window.requestAnimationFrame(step);
    }, COUNT_DELAY);
})();
