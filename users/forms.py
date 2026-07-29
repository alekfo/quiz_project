from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import User

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