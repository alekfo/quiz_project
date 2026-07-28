from django.shortcuts import render
from django.core.cache import cache
from django.contrib import messages
from django.shortcuts import render, redirect
from django.contrib.auth import views as auth_views



_REGISTER_RATE_LIMIT = 5  # попыток регистрации с одного IP за час
_LOGIN_RATE_LIMIT = 3  # неудачных попыток входа с одного IP за час

def _client_ip(request):
    """IP клиента: X-Real-IP (выставляется nginx), fallback — REMOTE_ADDR."""
    return request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR', '')

class RateLimitedLoginView(auth_views.LoginView):
    """LoginView с защитой от перебора пароля: не более _LOGIN_RATE_LIMIT неудачных
    попыток входа с одного IP в час. Успешные входы счётчик не увеличивают, чтобы
    не блокировать пользователей за общим IP (офис, NAT)."""

    def post(self, request, *args, **kwargs):
        ip = _client_ip(request)
        rate_key = f'login_attempts_{ip}'
        if cache.get(rate_key, 0) >= _LOGIN_RATE_LIMIT:
            messages.error(request, 'Слишком много попыток входа с вашего адреса. Попробуйте позже.')
            return redirect('users:login')
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        ip = _client_ip(self.request)
        rate_key = f'login_attempts_{ip}'
        cache.set(rate_key, cache.get(rate_key, 0) + 1, timeout=30)
        return super().form_invalid(form)