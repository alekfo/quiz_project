from django.urls import reverse

from quiz_project.testing import BaseTestCase, make_series, make_user
from social.models import QuizSeriesLike

HTMX = {"HTTP_HX_REQUEST": "true"}


class HtmxLoginRedirectMiddlewareTests(BaseTestCase):
    """
    Для HTMX-запроса редирект на логин должен превращаться в 204 + HX-Redirect:
    иначе XHR прозрачно следует за 302 и HTMX вставляет страницу логина внутрь кнопки.
    """

    def setUp(self):
        super().setUp()
        self.user = make_user("alice")
        self.series = make_series(make_user("author"), status="public")
        self.like_url = reverse("social:create_like", kwargs={"series_id": self.series.pk})

    def test_htmx_request_of_anonymous_gets_204_with_hx_redirect(self):
        response = self.client.post(
            self.like_url, HTTP_HX_CURRENT_URL="http://testserver/quizzes/2/preview?tab=1", **HTMX,
        )
        self.assertEqual(response.status_code, 204)
        # next - страница, где стояла кнопка, а не POST-only URL лайка (иначе после логина 405)
        self.assertEqual(response["HX-Redirect"], "/users/login/?next=/quizzes/2/preview%3Ftab%3D1")
        self.assertFalse(QuizSeriesLike.objects.exists())

    def test_next_falls_back_to_root_without_current_url(self):
        response = self.client.post(self.like_url, **HTMX)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], "/users/login/?next=/")

    def test_class_based_view_is_covered_too(self):
        """LoginRequiredMixin даёт такой же 302 на LOGIN_URL, как и @login_required."""
        response = self.client.get(reverse("quizzes:quizzes_list"), **HTMX)
        self.assertEqual(response.status_code, 204)
        self.assertIn("HX-Redirect", response)

    def test_regular_request_of_anonymous_keeps_302(self):
        response = self.client.post(self.like_url)
        self.assertLoginRequired(response)
        self.assertNotIn("HX-Redirect", response)

    def test_htmx_redirect_not_to_login_is_untouched(self):
        response = self.client.get("/", **HTMX)
        self.assertRedirects(response, reverse("quizzes:menu"), fetch_redirect_response=False)
        self.assertNotIn("HX-Redirect", response)

    def test_htmx_request_of_authenticated_user_is_untouched(self):
        self.client.force_login(self.user)
        response = self.client.post(self.like_url, **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(QuizSeriesLike.objects.filter(user=self.user, series=self.series).exists())
