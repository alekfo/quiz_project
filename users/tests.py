import os
import shutil
import tempfile
import time
from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail, signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from quiz_project.testing import PASSWORD, BaseTestCase, make_series, make_user
from social.models import Follow

from .forms import RegisterForm
from .views import _LOGIN_RATE_LIMIT, _REGISTER_RATE_LIMIT

User = get_user_model()


def make_uploaded_image(name: str = "avatar.png", size=(300, 300), color=(10, 20, 30),
                        fmt: str = "PNG") -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    buffer.seek(0)
    content_type = "image/png" if fmt == "PNG" else f"image/{fmt.lower()}"
    return SimpleUploadedFile(name, buffer.read(), content_type=content_type)


def register_data(**kwargs) -> dict:
    data = {
        "username": "newuser",
        "email": "newuser@example.com",
        "password1": "Str0ng-pass-word",
        "password2": "Str0ng-pass-word",
        "privacy_policy": "on",
        "website": "",
    }
    data.update(kwargs)
    return data


class RegisterFormTests(BaseTestCase):
    def test_valid_form_creates_custom_user(self):
        form = RegisterForm(register_data())
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertIsInstance(user, User)
        self.assertEqual(user.email, "newuser@example.com")
        self.assertTrue(user.check_password("Str0ng-pass-word"))
        self.assertFalse(user.email_confirmed)

    def test_disposable_email_domain_is_rejected(self):
        form = RegisterForm(register_data(email="bot@Mailinator.com"))
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_duplicate_email_is_rejected(self):
        make_user("existing", email="newuser@example.com")
        form = RegisterForm(register_data())
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_filled_honeypot_is_rejected(self):
        form = RegisterForm(register_data(website="http://spam.example"))
        self.assertFalse(form.is_valid())
        self.assertIn("website", form.errors)

    def test_privacy_policy_is_required(self):
        data = register_data()
        del data["privacy_policy"]
        form = RegisterForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("privacy_policy", form.errors)

    def test_password_mismatch_is_rejected(self):
        form = RegisterForm(register_data(password2="another-pass-word"))
        self.assertFalse(form.is_valid())
        self.assertIn("password2", form.errors)


@override_settings(SUPPORT_EMAIL="support@example.com", EMAIL_HOST_USER="noreply@example.com")
class RegisterViewTests(BaseTestCase):
    url = reverse("users:register")

    def test_get_renders_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context["form"], RegisterForm)

    def test_success_creates_user_logs_in_and_sends_emails(self):
        response = self.client.post(self.url, register_data())
        self.assertRedirects(response, reverse("quizzes:menu"), fetch_redirect_response=False)

        user = User.objects.get(username="newuser")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

        # письмо админу о новом пользователе и письмо-подтверждение самому пользователю
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, ["support@example.com"])
        confirmation = mail.outbox[1]
        self.assertEqual(confirmation.to, ["newuser@example.com"])
        self.assertIn("/users/confirm-email/?token=", confirmation.body)

    def test_confirmation_link_from_email_confirms_user(self):
        self.client.post(self.url, register_data())
        body = mail.outbox[1].body
        link = next(line for line in body.splitlines() if "/users/confirm-email/" in line)
        path = link[link.index("/users/confirm-email/"):]
        self.client.get(path)
        self.assertTrue(User.objects.get(username="newuser").email_confirmed)

    def test_registration_survives_confirmation_email_failure(self):
        with mock.patch("users.views._send_confirmation_email", side_effect=OSError("smtp down")):
            response = self.client.post(self.url, register_data())
        self.assertRedirects(response, reverse("quizzes:menu"), fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(username="newuser").exists())

    def test_invalid_form_does_not_create_user(self):
        response = self.client.post(self.url, register_data(website="spam"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_rate_limit_blocks_after_limit_attempts(self):
        for _ in range(_REGISTER_RATE_LIMIT):
            self.client.post(self.url, register_data(website="spam"))
        # лимит исчерпан - даже корректные данные не создают пользователя
        response = self.client.post(self.url, register_data())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_rate_limit_is_counted_per_ip(self):
        for _ in range(_REGISTER_RATE_LIMIT):
            self.client.post(self.url, register_data(website="spam"))
        response = self.client.post(self.url, register_data(), HTTP_X_REAL_IP="10.0.0.7")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="newuser").exists())


class ConfirmEmailTests(BaseTestCase):
    url = reverse("users:confirm_email")

    def setUp(self):
        super().setUp()
        self.user = make_user("alice")

    def test_valid_token_confirms_email(self):
        token = signing.dumps({"uid": self.user.pk}, salt="email-confirm")
        response = self.client.get(self.url, {"token": token})
        self.assertRedirects(response, reverse("quizzes:menu"), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_confirmed)

    def test_garbage_token_does_not_confirm(self):
        response = self.client.get(self.url, {"token": "garbage"})
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_confirmed)

    def test_token_signed_with_another_salt_does_not_confirm(self):
        token = signing.dumps({"uid": self.user.pk}, salt="another-salt")
        self.client.get(self.url, {"token": token})
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_confirmed)

    def test_expired_token_does_not_confirm(self):
        token = signing.dumps({"uid": self.user.pk}, salt="email-confirm")
        # токен живёт 24 часа - сдвигаем "сейчас" на 25 часов вперёд
        with mock.patch("django.core.signing.time.time", return_value=time.time() + 25 * 3600):
            self.client.get(self.url, {"token": token})
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_confirmed)

    def test_token_for_missing_user_does_not_crash(self):
        token = signing.dumps({"uid": 999_999}, salt="email-confirm")
        response = self.client.get(self.url, {"token": token})
        self.assertEqual(response.status_code, 302)


class LoginTests(BaseTestCase):
    url = reverse("users:login")

    def setUp(self):
        super().setUp()
        self.user = make_user("alice")

    def login(self, password=PASSWORD, **extra):
        return self.client.post(self.url, {"username": "alice", "password": password}, **extra)

    def test_successful_login_redirects_to_menu(self):
        response = self.login()
        self.assertRedirects(response, "/quizzes/menu/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_wrong_password_does_not_log_in(self):
        response = self.login(password="wrong")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_rate_limit_blocks_even_correct_password(self):
        for _ in range(_LOGIN_RATE_LIMIT):
            self.login(password="wrong")
        response = self.login()
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_successful_logins_do_not_count_towards_limit(self):
        for _ in range(_LOGIN_RATE_LIMIT + 2):
            self.login()
            self.client.logout()
        self.assertEqual(self.login().status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_rate_limit_is_counted_per_ip(self):
        for _ in range(_LOGIN_RATE_LIMIT):
            self.login(password="wrong")
        self.login(HTTP_X_REAL_IP="10.0.0.7")
        self.assertIn("_auth_user_id", self.client.session)

    def test_authenticated_user_is_redirected_from_login_page(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(self.url), "/quizzes/menu/", fetch_redirect_response=False)

    def test_logout_requires_post_and_redirects_to_login(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("users:logout"))
        self.assertRedirects(response, "/users/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)


class UserPagesTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.me = make_user("me")
        self.other = make_user("other")

    def test_private_pages_require_login(self):
        urls = [
            reverse("users:users_list"),
            reverse("users:about_me"),
            reverse("users:settings"),
            reverse("users:user_detail", kwargs={"pk": self.other.pk}),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertLoginRequired(self.client.get(url))

    def test_static_pages_are_open_for_anonymous(self):
        for name in ("users:privacy_policy", "users:public_offer", "users:feedback"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_private_pages_render_for_authenticated_user(self):
        self.client.force_login(self.me)
        for name in ("users:users_list", "users:about_me", "users:settings"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_users_list_is_ordered_by_username(self):
        make_user("alpha")
        self.client.force_login(self.me)
        response = self.client.get(reverse("users:users_list"))
        self.assertEqual([u.username for u in response.context["users"]], ["alpha", "me", "other"])

    def test_foreign_profile_context(self):
        public = make_series(self.other, status="public", title="public")
        make_series(self.other, status="private", title="private")
        Follow.objects.create(follower=self.me, following=self.other)
        self.client.force_login(self.me)

        response = self.client.get(reverse("users:user_detail", kwargs={"pk": self.other.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["target_user"], self.other)
        # объект профиля не должен затенять залогиненного пользователя (шапка base.html)
        self.assertEqual(response.context["user"], self.me)
        self.assertTrue(response.context["followed"])
        self.assertFalse(response.context["is_it_me"])
        # на профиле видны только публичные серии автора
        self.assertCountEqual(response.context["public_series"], [public])
        self.assertContains(response, "public")
        # email чужого пользователя на публичном профиле не показывается
        self.assertNotContains(response, self.other.email)

    def test_own_profile_context(self):
        self.client.force_login(self.me)
        response = self.client.get(reverse("users:user_detail", kwargs={"pk": self.me.pk}))
        self.assertTrue(response.context["is_it_me"])
        self.assertFalse(response.context["followed"])

    def test_missing_profile_is_404(self):
        self.client.force_login(self.me)
        response = self.client.get(reverse("users:user_detail", kwargs={"pk": 999_999}))
        self.assertEqual(response.status_code, 404)


class ProfileUpdateViewTests(BaseTestCase):
    """URL без pk - get_object() всегда возвращает request.user, подделать нечем."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix="quiz_test_media_")
        cls.addClassCleanup(shutil.rmtree, cls._media_root, ignore_errors=True)
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()
        cls.addClassCleanup(cls._media_override.disable)

    def setUp(self):
        super().setUp()
        self.me = make_user("me")
        self.client.force_login(self.me)

    def test_requires_login(self):
        self.client.logout()
        self.assertLoginRequired(self.client.get(reverse("users:profile_update")))

    def test_get_renders_own_data(self):
        self.me.first_name = "Имя"
        self.me.save(update_fields=["first_name"])
        response = self.client.get(reverse("users:profile_update"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].instance, self.me)

    def test_updates_names(self):
        response = self.client.post(reverse("users:profile_update"),
                                     {"first_name": "Новое", "last_name": "Имя"})
        self.assertRedirects(response, reverse("users:about_me"))
        self.me.refresh_from_db()
        self.assertEqual(self.me.first_name, "Новое")
        self.assertEqual(self.me.last_name, "Имя")

    def test_uploaded_avatar_is_resized_and_reencoded(self):
        upload = make_uploaded_image(size=(2000, 2000))
        self.client.post(reverse("users:profile_update"),
                          {"first_name": "", "last_name": "", "avatar": upload})
        self.me.refresh_from_db()
        self.assertTrue(self.me.avatar.name.endswith(".jpg"))
        with Image.open(self.me.avatar.path) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(max(image.size), 512)

    def test_replacing_avatar_deletes_old_file(self):
        self.client.post(reverse("users:profile_update"),
                          {"first_name": "", "last_name": "", "avatar": make_uploaded_image("a.png")})
        self.me.refresh_from_db()
        old_path = self.me.avatar.path
        self.assertTrue(old_path and os.path.exists(old_path))

        self.client.post(reverse("users:profile_update"),
                          {"first_name": "", "last_name": "", "avatar": make_uploaded_image("b.png")})
        self.me.refresh_from_db()
        self.assertNotEqual(self.me.avatar.path, old_path)
        self.assertFalse(os.path.exists(old_path))

    def test_submit_without_touching_avatar_keeps_it(self):
        self.client.post(reverse("users:profile_update"),
                          {"first_name": "", "last_name": "", "avatar": make_uploaded_image()})
        self.me.refresh_from_db()
        avatar_name = self.me.avatar.name

        self.client.post(reverse("users:profile_update"), {"first_name": "Другое", "last_name": ""})
        self.me.refresh_from_db()
        self.assertEqual(self.me.avatar.name, avatar_name)
        self.assertEqual(self.me.first_name, "Другое")

    def test_clear_checkbox_removes_avatar_and_deletes_file(self):
        self.client.post(reverse("users:profile_update"),
                          {"first_name": "", "last_name": "", "avatar": make_uploaded_image()})
        self.me.refresh_from_db()
        old_path = self.me.avatar.path

        self.client.post(reverse("users:profile_update"),
                          {"first_name": "", "last_name": "", "avatar-clear": "on"})
        self.me.refresh_from_db()
        self.assertFalse(self.me.avatar)
        self.assertFalse(os.path.exists(old_path))

    def test_oversized_avatar_is_rejected_without_changes(self):
        # Случайный шум - валидный PNG, который почти не сжимается (сплошной цвет
        # сжался бы до пары килобайт и не превысил лимит даже на большом разрешении).
        buffer = BytesIO()
        Image.frombytes("RGB", (1700, 1700), os.urandom(1700 * 1700 * 3)).save(
            buffer, format="PNG", compress_level=0
        )
        buffer.seek(0)
        huge = SimpleUploadedFile("huge.png", buffer.read(), content_type="image/png")

        response = self.client.post(reverse("users:profile_update"),
                                     {"first_name": "", "last_name": "", "avatar": huge})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "avatar", "Файл слишком большой: максимум 5 МБ.")
        self.me.refresh_from_db()
        self.assertFalse(self.me.avatar)

    def test_another_users_profile_is_not_affected(self):
        other = make_user("other")
        self.client.post(reverse("users:profile_update"),
                          {"first_name": "Моё", "last_name": "", "avatar": make_uploaded_image()})
        other.refresh_from_db()
        self.assertEqual(other.first_name, "")
        self.assertFalse(other.avatar)
