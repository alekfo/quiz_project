(() => {
    "use strict";

    const checkbox = document.getElementById("id_dynamic_scoring_mode");
    const points = document.getElementById("id_points_per_correct");
    if (!checkbox || !points || !checkbox.form) return;

    const syncScoring = () => {
        points.disabled = checkbox.checked;
        points.required = !checkbox.checked;
    };

    checkbox.addEventListener("change", syncScoring);
    checkbox.form.addEventListener("reset", () => setTimeout(syncScoring, 0));
    window.addEventListener("pageshow", syncScoring);
    syncScoring();
})();
