from django.db import IntegrityError, transaction
from django.urls import reverse

from quiz_project.testing import BaseTestCase, make_series, make_user
from quizzes.models import QuizSeries

from .models import Follow, QuizSeriesLike, SavedQuizSeries
from .services import get_follow_context, get_like_context, get_save_context

HTMX = {"HTTP_HX_REQUEST": "true"}


class ModelConstraintTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.series = make_series(self.alice, status="public")

    def assertIntegrityError(self, create):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create()

    def test_follow_is_unique(self):
        Follow.objects.create(follower=self.alice, following=self.bob)
        self.assertIntegrityError(lambda: Follow.objects.create(follower=self.alice, following=self.bob))

    def test_cannot_follow_self_on_db_level(self):
        self.assertIntegrityError(lambda: Follow.objects.create(follower=self.alice, following=self.alice))

    def test_follow_in_opposite_direction_is_allowed(self):
        Follow.objects.create(follower=self.alice, following=self.bob)
        Follow.objects.create(follower=self.bob, following=self.alice)
        self.assertEqual(Follow.objects.count(), 2)

    def test_like_is_unique_per_user_and_series(self):
        QuizSeriesLike.objects.create(series=self.series, user=self.bob)
        self.assertIntegrityError(lambda: QuizSeriesLike.objects.create(series=self.series, user=self.bob))

    def test_saved_series_is_unique_per_user_and_series(self):
        SavedQuizSeries.objects.create(series=self.series, user=self.bob)
        self.assertIntegrityError(lambda: SavedQuizSeries.objects.create(series=self.series, user=self.bob))

    def test_deleting_series_removes_likes_and_saves(self):
        QuizSeriesLike.objects.create(series=self.series, user=self.bob)
        SavedQuizSeries.objects.create(series=self.series, user=self.bob)
        self.series.delete()
        self.assertFalse(QuizSeriesLike.objects.exists())
        self.assertFalse(SavedQuizSeries.objects.exists())


class ServicesTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.series = make_series(self.alice, status="public")

    def test_follow_context(self):
        Follow.objects.create(follower=self.bob, following=self.alice)
        context = get_follow_context(self.bob, self.alice)
        self.assertEqual(context, {"target_user": self.alice, "followed": True, "is_it_me": False})
        # подписка не симметрична
        self.assertFalse(get_follow_context(self.alice, self.bob)["followed"])
        self.assertTrue(get_follow_context(self.alice, self.alice)["is_it_me"])

    def test_like_context(self):
        QuizSeriesLike.objects.create(series=self.series, user=self.bob)
        QuizSeriesLike.objects.create(series=self.series, user=make_user("carol"))
        self.assertEqual(
            get_like_context(self.bob, self.series),
            {"series": self.series, "nums_of_likes": 2, "already_liked": True},
        )
        self.assertFalse(get_like_context(self.alice, self.series)["already_liked"])

    def test_save_context(self):
        SavedQuizSeries.objects.create(series=self.series, user=self.bob)
        self.assertEqual(
            get_save_context(self.bob, self.series),
            {"series": self.series, "already_saved": True, "is_my_series": False},
        )
        self.assertEqual(
            get_save_context(self.alice, self.series),
            {"series": self.series, "already_saved": False, "is_my_series": True},
        )


class AccessRulesTests(BaseTestCase):
    """Общие для всех шести вьюх правила: только POST и только залогиненным."""

    def setUp(self):
        super().setUp()
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.series = make_series(self.alice, status="public")
        self.urls = [
            reverse("social:follow", kwargs={"user_id": self.alice.pk}),
            reverse("social:unfollow", kwargs={"user_id": self.alice.pk}),
            reverse("social:create_like", kwargs={"series_id": self.series.pk}),
            reverse("social:delete_like", kwargs={"series_id": self.series.pk}),
            reverse("social:save_series", kwargs={"series_id": self.series.pk}),
            reverse("social:unsave_series", kwargs={"series_id": self.series.pk}),
        ]

    def test_anonymous_is_redirected_to_login_and_nothing_changes(self):
        for url in self.urls:
            with self.subTest(url=url):
                self.assertLoginRequired(self.client.post(url))
        self.assertFalse(Follow.objects.exists())
        self.assertFalse(QuizSeriesLike.objects.exists())
        self.assertFalse(SavedQuizSeries.objects.exists())

    def test_get_is_not_allowed(self):
        """Удаление по GET обходило бы CSRF - все вьюхи должны отвечать 405."""
        Follow.objects.create(follower=self.bob, following=self.alice)
        QuizSeriesLike.objects.create(series=self.series, user=self.bob)
        SavedQuizSeries.objects.create(series=self.series, user=self.bob)
        self.client.force_login(self.bob)
        for url in self.urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Follow.objects.count(), 1)
        self.assertEqual(QuizSeriesLike.objects.count(), 1)
        self.assertEqual(SavedQuizSeries.objects.count(), 1)


class FollowViewTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.follow_url = reverse("social:follow", kwargs={"user_id": self.alice.pk})
        self.unfollow_url = reverse("social:unfollow", kwargs={"user_id": self.alice.pk})
        self.client.force_login(self.bob)

    def test_follow_creates_row_and_redirects_to_profile(self):
        response = self.client.post(self.follow_url)
        self.assertRedirects(
            response, reverse("users:user_detail", kwargs={"pk": self.alice.pk}),
            fetch_redirect_response=False,
        )
        self.assertTrue(Follow.objects.filter(follower=self.bob, following=self.alice).exists())

    def test_follow_is_idempotent(self):
        self.client.post(self.follow_url)
        response = self.client.post(self.follow_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Follow.objects.count(), 1)

    def test_cannot_follow_self(self):
        response = self.client.post(reverse("social:follow", kwargs={"user_id": self.bob.pk}))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Follow.objects.exists())

    def test_follow_missing_user_is_404(self):
        response = self.client.post(reverse("social:follow", kwargs={"user_id": 999_999}))
        self.assertEqual(response.status_code, 404)

    def test_unfollow_deletes_only_own_follow(self):
        Follow.objects.create(follower=self.bob, following=self.alice)
        carol = make_user("carol")
        Follow.objects.create(follower=carol, following=self.alice)
        self.client.post(self.unfollow_url)
        self.assertFalse(Follow.objects.filter(follower=self.bob).exists())
        self.assertTrue(Follow.objects.filter(follower=carol, following=self.alice).exists())

    def test_unfollow_without_follow_is_not_an_error(self):
        self.assertEqual(self.client.post(self.unfollow_url).status_code, 302)

    def test_htmx_request_returns_button_partial(self):
        response = self.client.post(self.follow_url, **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "social/_follow_button.html")
        self.assertTrue(response.context["followed"])
        # в новой форме должен быть CSRF-токен, иначе следующий клик получит 403
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, self.unfollow_url)

        response = self.client.post(self.unfollow_url, **HTMX)
        self.assertFalse(response.context["followed"])
        self.assertContains(response, self.follow_url)


class LikeViewTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.author = make_user("author")
        self.reader = make_user("reader")
        self.public = make_series(self.author, status="public")
        self.private = make_series(self.author, status="private")
        self.client.force_login(self.reader)

    def like_url(self, series):
        return reverse("social:create_like", kwargs={"series_id": series.pk})

    def unlike_url(self, series):
        return reverse("social:delete_like", kwargs={"series_id": series.pk})

    def test_like_public_series_and_redirect_to_preview(self):
        response = self.client.post(self.like_url(self.public))
        self.assertRedirects(
            response, reverse("quizzes:quizzes_preview", kwargs={"pk": self.public.pk}),
            fetch_redirect_response=False,
        )
        self.assertTrue(QuizSeriesLike.objects.filter(series=self.public, user=self.reader).exists())

    def test_like_is_idempotent(self):
        self.client.post(self.like_url(self.public))
        self.client.post(self.like_url(self.public))
        self.assertEqual(QuizSeriesLike.objects.count(), 1)

    def test_like_foreign_private_series_is_404(self):
        response = self.client.post(self.like_url(self.private))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(QuizSeriesLike.objects.exists())

    def test_author_can_like_own_private_series(self):
        self.client.force_login(self.author)
        self.client.post(self.like_url(self.private))
        self.assertTrue(QuizSeriesLike.objects.filter(series=self.private, user=self.author).exists())

    def test_unlike_deletes_only_own_like(self):
        QuizSeriesLike.objects.create(series=self.public, user=self.reader)
        QuizSeriesLike.objects.create(series=self.public, user=self.author)
        self.client.post(self.unlike_url(self.public))
        self.assertFalse(QuizSeriesLike.objects.filter(user=self.reader).exists())
        self.assertTrue(QuizSeriesLike.objects.filter(user=self.author).exists())

    def test_unlike_without_like_is_not_an_error(self):
        self.assertEqual(self.client.post(self.unlike_url(self.public)).status_code, 302)

    def test_like_stays_when_series_becomes_private(self):
        """Принятое решение: delete_like на visible_to - лайк на ставшей приватной серии не снять."""
        QuizSeriesLike.objects.create(series=self.public, user=self.reader)
        QuizSeries.objects.filter(pk=self.public.pk).update(status="private")
        self.assertEqual(self.client.post(self.unlike_url(self.public)).status_code, 404)
        self.assertTrue(QuizSeriesLike.objects.filter(series=self.public, user=self.reader).exists())

    def test_htmx_request_returns_button_partial_with_counter(self):
        QuizSeriesLike.objects.create(series=self.public, user=self.author)
        response = self.client.post(self.like_url(self.public), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "social/_like_button.html")
        self.assertEqual(response.context["nums_of_likes"], 2)
        self.assertTrue(response.context["already_liked"])
        self.assertContains(response, "csrfmiddlewaretoken")

        response = self.client.post(self.unlike_url(self.public), **HTMX)
        self.assertEqual(response.context["nums_of_likes"], 1)
        self.assertFalse(response.context["already_liked"])


class SaveSeriesViewTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.author = make_user("author")
        self.reader = make_user("reader")
        self.public = make_series(self.author, status="public")
        self.private = make_series(self.author, status="private")
        self.client.force_login(self.reader)

    def save_url(self, series):
        return reverse("social:save_series", kwargs={"series_id": series.pk})

    def unsave_url(self, series):
        return reverse("social:unsave_series", kwargs={"series_id": series.pk})

    def test_save_foreign_public_series(self):
        response = self.client.post(self.save_url(self.public))
        self.assertRedirects(
            response, reverse("quizzes:quizzes_preview", kwargs={"pk": self.public.pk}),
            fetch_redirect_response=False,
        )
        self.assertTrue(SavedQuizSeries.objects.filter(series=self.public, user=self.reader).exists())

    def test_save_is_idempotent(self):
        self.client.post(self.save_url(self.public))
        self.client.post(self.save_url(self.public))
        self.assertEqual(SavedQuizSeries.objects.count(), 1)

    def test_cannot_save_own_series(self):
        self.client.force_login(self.author)
        for series in (self.public, self.private):
            with self.subTest(status=series.status):
                self.assertEqual(self.client.post(self.save_url(series)).status_code, 403)
        self.assertFalse(SavedQuizSeries.objects.exists())

    def test_save_foreign_private_series_is_404(self):
        self.assertEqual(self.client.post(self.save_url(self.private)).status_code, 404)
        self.assertFalse(SavedQuizSeries.objects.exists())

    def test_unsave_deletes_only_own_row(self):
        other = make_user("other")
        SavedQuizSeries.objects.create(series=self.public, user=self.reader)
        SavedQuizSeries.objects.create(series=self.public, user=other)
        self.client.post(self.unsave_url(self.public))
        self.assertFalse(SavedQuizSeries.objects.filter(user=self.reader).exists())
        self.assertTrue(SavedQuizSeries.objects.filter(user=other).exists())

    def test_unsave_without_saved_row_is_not_an_error(self):
        self.assertEqual(self.client.post(self.unsave_url(self.public)).status_code, 302)

    def test_saved_row_stays_when_series_becomes_private(self):
        """Принятое решение: unsave на visible_to - строка остаётся и скрывается фильтром при чтении."""
        SavedQuizSeries.objects.create(series=self.public, user=self.reader)
        QuizSeries.objects.filter(pk=self.public.pk).update(status="private")
        self.assertEqual(self.client.post(self.unsave_url(self.public)).status_code, 404)
        self.assertTrue(SavedQuizSeries.objects.filter(series=self.public, user=self.reader).exists())
        self.assertFalse(QuizSeries.objects.saved_by_user(self.reader).exists())

    def test_htmx_request_returns_button_partial(self):
        response = self.client.post(self.save_url(self.public), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "social/_save_button.html")
        self.assertTrue(response.context["already_saved"])
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, self.unsave_url(self.public))

        response = self.client.post(self.unsave_url(self.public), **HTMX)
        self.assertFalse(response.context["already_saved"])
        self.assertContains(response, self.save_url(self.public))
