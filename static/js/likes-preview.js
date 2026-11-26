document.querySelectorAll('[data-like-sample]').forEach((sample) => {
    const button = sample.querySelector('[data-like-toggle]');
    const counter = sample.querySelector('[data-like-counter]');
    const counts = sample.querySelectorAll('[data-like-count]');
    const initialCount = Number(counter.querySelector('[data-like-count]').textContent);

    button.addEventListener('click', () => {
        const liked = button.getAttribute('aria-pressed') === 'false';
        const count = initialCount + Number(liked);
        const action = liked ? 'Убрать лайк' : 'Лайкнуть';

        sample.toggleAttribute('data-liked', liked);
        button.setAttribute('aria-pressed', String(liked));
        button.setAttribute('aria-label', `${action}. Лайков: ${count}`);
        counter.setAttribute('aria-label', `Лайков: ${count}`);
        counts.forEach((element) => {
            element.textContent = count;
        });
    });
});
