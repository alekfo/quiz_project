from io import BytesIO
from uuid import uuid4

from PIL import Image

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from .models import User
from django.contrib.auth import get_user_model

# Ограничения на загружаемый аватар.
_AVATAR_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 МБ - лимит на входящий файл
_AVATAR_MAX_DIMENSION = 512  # пикселей по длинной стороне после ресайза

# Домены одноразовой почты, через которые часто фармят бесплатные аккаунты.
_DISPOSABLE_EMAIL_DOMAINS = frozenset({
    'mailinator.com', 'guerrillamail.com', '10minutemail.com', 'tempmail.com',
    'temp-mail.org', 'yopmail.com', 'trashmail.com', '1secmail.com',
    'sharklasers.com', 'getnada.com', 'maildrop.cc', 'discard.email',
})

class RegisterForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'placeholder': 'example@mail.com'}),
        label='Email',
    )
    privacy_policy = forms.BooleanField(
        required=True,
        error_messages={'required': 'Необходимо принять политику конфиденциальности для регистрации.'},
    )
    # Honeypot: невидимое для людей поле. Простые боты заполняют все input'ы
    # формы автоматически, человек его не видит и не трогает.
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'autocomplete': 'off',
        'tabindex': '-1',
    }))

    class Meta:
        # model переопределён неспроста: у родителя (BaseUserCreationForm в
        # django/contrib/auth/forms.py) Meta.model = django.contrib.auth.models.User —
        # СТАНДАРТНАЯ модель Django, а не наша кастомная (AUTH_USER_MODEL = 'users.User').
        # Без этого переопределения форма создавала бы инстансы не той модели.
        model = User
        # fields управляет только тем, какие ПОЛЯ МОДЕЛИ автогенерируются как поля формы
        # (username здесь — модельное поле; password1/password2 уже объявлены явно на
        # родительском классе, их включение сюда чисто для наглядности).
        # На поля, объявленные прямо на классе формы (email — тоже модельное и явно
        # переопределённое; website, privacy_policy — вообще не из модели), fields не
        # влияет: такие поля всегда попадают в форму. Разница в другом — form.save()
        # берёт cleaned_data только по ключам из fields, поэтому email (он в fields)
        # запишется в user.email, а website/privacy_policy (их в fields нет и не может
        # быть — таких колонок в User нет) в модель не попадут никогда, даже при save().
        fields = ('username', 'email', 'password1', 'password2')

    def clean_website(self):
        """Honeypot-валидация: отклоняет форму если невидимое поле заполнено (признак бота)."""
        if self.cleaned_data.get('website'):
            raise forms.ValidationError('Ошибка валидации формы.')
        return ''

    def clean_email(self):
        """Проверяет уникальность email и отклоняет домены одноразовых почт."""
        email = self.cleaned_data.get('email')
        domain = email.rsplit('@', 1)[-1].lower() if email and '@' in email else ''
        if domain in _DISPOSABLE_EMAIL_DOMAINS:
            raise forms.ValidationError('Временные почтовые адреса не поддерживаются.')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('Пользователь с таким email уже зарегистрирован.')
        return email

class ProfileUpdateForm(forms.ModelForm):
    """Редактирование собственного профиля: имя, фамилия, аватар."""

    class Meta:
        model = get_user_model()
        fields = "first_name", "last_name", "avatar"
        widgets = {
            'avatar': forms.ClearableFileInput(attrs={'accept': 'image/*'}),
        }
        labels = {
            'first_name': 'Ваше имя',
            'last_name': 'Ваша фамилия',
            'avatar': 'Аватар',
        }

    def clean_avatar(self):
        """Ограничивает размер загружаемого файла и приводит картинку к единому
        формату/размеру (до 512x512, JPEG) - чтобы в списках и шапке не грузились
        оригиналы в несколько мегапикселей и не копился произвольный формат файлов.

        cleaned_data здесь - это либо новая загрузка (UploadedFile), либо прежнее
        значение без изменений, либо False/''  (пользователь нажал "очистить") -
        ресайзить и проверять размер нужно только в первом случае.
        """
        avatar = self.cleaned_data.get('avatar')
        if not isinstance(avatar, UploadedFile):
            return avatar

        if avatar.size > _AVATAR_MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f'Файл слишком большой: максимум {_AVATAR_MAX_UPLOAD_BYTES // (1024 * 1024)} МБ.'
            )

        # Django ImageField уже проверил выше (на уровне поля формы), что это
        # валидное изображение (включая защиту Pillow от decompression bomb) -
        # здесь повторно открываем тот же файл, чтобы уменьшить и перекодировать.
        image = Image.open(avatar)
        if image.mode in ('RGBA', 'LA', 'P'):
            # Прозрачность не имеет смысла в JPEG - подкладываем белый фон,
            # иначе прозрачные области почернеют.
            rgba = image.convert('RGBA')
            background = Image.new('RGB', rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        else:
            image = image.convert('RGB')
        image.thumbnail((_AVATAR_MAX_DIMENSION, _AVATAR_MAX_DIMENSION), Image.LANCZOS)

        buffer = BytesIO()
        image.save(buffer, format='JPEG', quality=85)
        return ContentFile(buffer.getvalue(), name=f'{uuid4().hex}.jpg')