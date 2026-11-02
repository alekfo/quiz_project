from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.http import HttpResponse, QueryDict


class HtmxLoginRedirectMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.headers.get("HX-Request") != "true":
            return response
        if response.status_code != 302:
            return response
        if urlparse(response["Location"]).path != settings.LOGIN_URL:
            return response

        # next берём из страницы, где стояла кнопка, а не из request.path:
        # иначе после логина вернёт на POST-only URL лайка -> 405
        current = urlparse(request.headers.get("HX-Current-URL", ""))
        next_url = current.path or "/"
        if current.query:
            next_url += "?" + current.query

        qs = QueryDict(mutable=True)
        qs[REDIRECT_FIELD_NAME] = next_url
        return HttpResponse(
            status=204,
            headers={"HX-Redirect": f"{settings.LOGIN_URL}?{qs.urlencode(safe='/')}"},
        )