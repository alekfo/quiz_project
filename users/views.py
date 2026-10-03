import logging

from django.http import HttpRequest, HttpResponse
from django.core.cache import cache
from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib.auth import views as auth_views
from django.contrib.auth import login, get_user_model
from django.core.mail import send_mail
from django.core import signing
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView, ListView, DetailView, UpdateView

from .forms import RegisterForm, ProfileUpdateForm
from social.services import get_follow_context
from quizzes.models import QuizSeries

logger = logging.getLogger(__name__)

_REGISTER_RATE_LIMIT = 3  # попыток регистрации с одного IP за час
_LOGIN_RATE_LIMIT = 3  # неудачных попыток входа с одного IP за час

def _notify_admin_new_user(user):
    """Отправляет уведомление на SUPPORT_EMAIL при регистрации нового пользователя. fail_silently."""

    try:
        send_mail(
            subject=f"Новый пользователь в quiz_profect: {user.username}",
            message=(
                f"Зарегистрировался новый пользователь.\n\n"
                f"Логин: {user.username}\n"
                f"Email: {user.email or '—'}\n"
                f"Дата: {user.date_joined.strftime('%d.%m.%Y %H:%M UTC')}"
            ),
            from_email=settings.EMAIL_HOST_USER,
            recipient_list=[settings.SUPPORT_EMAIL],
            fail_silently=True,
        )
    except Exception:
        pass

def _send_confirmation_email(user, request: HttpRequest):
    """Отправляет письмо с токеном подтверждения email. Токен действителен 24 часа."""

    token = signing.dumps({'uid': user.pk}, salt='email-confirm')
    confirm_url = request.build_absolute_uri(f'/users/confirm-email/?token={token}')
    send_mail(
        subject='Подтверждение email — quiz_project',
        message=(
            f'Здравствуйте, {user.username}!\n\n'
            f'Для подтверждения адреса электронной почты перейдите по ссылке:\n'
            f'{confirm_url}\n\n'
            f'Ссылка действительна 24 часа.\n'
            f'Если вы не регистрировались в PrepStats — проигнорируйте письмо.'
        ),
        from_email=settings.EMAIL_HOST_USER,
        recipient_list=[user.email],
        fail_silently=False,
    )

def _client_ip(request: HttpRequest):
    """IP клиента: X-Real-IP (выставляется nginx), fallback — REMOTE_ADDR."""

    return request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR', '')


def confirm_email(request:HttpRequest):
    """Верифицирует токен из письма и устанавливает email_confirmed = True.

    Токен подписан через django.core.signing, TTL 24 часа.
    При истёкшем или недействительном токене показывает сообщение об ошибке.
    """

    token = request.GET.get('token', '')
    ip = _client_ip(request)
    try:
        data = signing.loads(token, salt='email-confirm', max_age=86400)
        User = get_user_model()
        user = User.objects.get(pk=data['uid'])
        user.email_confirmed = True
        user.save(update_fields=['email_confirmed'])
        logger.info("Email confirmed for user=%s ip=%s", user.username, ip)
        messages.success(request, 'Email успешно подтверждён!')
    except signing.SignatureExpired:
        logger.warning("Expired email confirmation token for token=%.20s… ip=%s", token, ip)
        messages.error(request, 'Ссылка истекла. Запросите новое письмо.')
    except (signing.BadSignature, Exception) as e:
        logger.warning("Invalid email confirmation token: %s ip=%s", e, ip)
        messages.error(request, 'Недействительная ссылка подтверждения.')
    return redirect('quizzes:menu')

class RateLimitedLoginView(auth_views.LoginView):
    """LoginView с защитой от перебора пароля: не более _LOGIN_RATE_LIMIT неудачных
    попыток входа с одного IP в час. Успешные входы счётчик не увеличивают, чтобы
    не блокировать пользователей за общим IP (офис, NAT)."""

    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        ip = _client_ip(request)
        rate_key = f'login_attempts_{ip}'
        if cache.get(rate_key, 0) >= _LOGIN_RATE_LIMIT:
            logger.warning("Login rate limit hit for ip=%s", ip)
            messages.error(request, 'Слишком много попыток входа с вашего адреса. Попробуйте позже.')
            return redirect('users:login')
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        ip = _client_ip(self.request)
        rate_key = f'login_attempts_{ip}'
        cache.set(rate_key, cache.get(rate_key, 0) + 1, timeout=3600)
        return super().form_invalid(form)

def register(request: HttpRequest):
    """Регистрация нового пользователя с rate-limit по IP (5 попыток/час).

    После успешной регистрации выполняет вход и отправляет письмо подтверждения email.
    """
    if request.method == 'POST':
        ip = _client_ip(request)
        rate_key = f'register_attempts_{ip}'
        attempts = cache.get(rate_key, 0)
        if attempts >= _REGISTER_RATE_LIMIT:
            logger.warning("Registration rate limit hit for ip=%s", ip)
            messages.error(request, 'Слишком много попыток регистрации с вашего адреса. Попробуйте позже.')
            return render(request, 'users/register.html', {'form': RegisterForm()})
        cache.set(rate_key, attempts + 1, timeout=3600)

        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            logger.info("New user registered: %s <%s> ip=%s", user.username, user.email, ip)
            _notify_admin_new_user(user)
            try:
                _send_confirmation_email(user, request)
                messages.info(
                    request,
                    f'Письмо с подтверждением отправлено на {user.email}. Если не видите — проверьте папку «Спам».',
                )
            except Exception as e:
                logger.warning("Confirmation email failed for user=%s: %s", user.username, e)
            return redirect('quizzes:menu')
    else:
        form = RegisterForm()
    return render(request, 'users/register.html', {'form': form})

def privacy_policy(request: HttpRequest):
    """Страница политики конфиденциальности."""
    return render(request, 'users/privacy_policy.html')

def public_offer(request: HttpRequest):
    """Страница договора публичной оферты."""
    return render(request, 'users/public_offer.html')

def feedback(request: HttpRequest):
    """Страница для обратной связи"""
    return render(request, 'users/feedback.html')

class AboutMeView(LoginRequiredMixin, TemplateView):
    """Посмотреть инфу о текущем пользователе"""

    template_name = "users/about_me.html"

class UsersListView(LoginRequiredMixin, ListView):
    """Посмотреть список всех пользователей"""

    model = get_user_model()
    template_name = 'users/users_list.html'
    context_object_name = 'users'
    ordering = ['username']


class UserDetailView(LoginRequiredMixin, DetailView):
    """Посмотреть детальную инфу о любом пользователе"""

    model = get_user_model()
    template_name = 'users/user_detail.html'
    context_object_name = 'target_user'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        #берем контекст из social.services.get_follow_context
        context.update(get_follow_context(self.request.user, self.object))
        context["public_series"] = QuizSeries.objects.filter(user=self.object, status="public")
        return context

class SettingsPageView(LoginRequiredMixin, TemplateView):
    """Страница настроек аккаунта: email, подтверждение, смена пароля, информация о подписке и пр."""

    template_name = "users/settings.html"

class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    """Редактирование собственного профиля (имя, фамилия, аватар).

    Без pk/slug в URL: get_object() всегда возвращает текущего пользователя,
    так что чужой профиль подстановкой pk в адрес не открыть и не изменить.
    """

    form_class = ProfileUpdateForm
    template_name = 'users/user_update_form.html'

    def get_object(self, queryset=None):
        return self.request.user

    def post(self, request, *args, **kwargs):
        # Запоминаем старое имя файла ДО is_valid(): ModelForm._post_clean()
        # мутирует instance (= self.object) уже внутри form.is_valid(), которая
        # выполняется раньше form_valid() - там self.object.avatar уже новый.
        user = self.get_object()
        self._old_avatar_name = user.avatar.name if user.avatar else None
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        # Старый файл аватара ImageField сам не удаляет при замене/очистке -
        # без этого в media/users/<pk>/avatar/ копились бы все версии навечно.
        response = super().form_valid(form)
        if self._old_avatar_name and self.object.avatar.name != self._old_avatar_name:
            self.object.avatar.storage.delete(self._old_avatar_name)
        logger.info('Профиль обновлён: username="%s"', self.request.user.username)
        messages.success(self.request, "Профиль обновлён")
        return response

    def get_success_url(self):
        return reverse("users:about_me")