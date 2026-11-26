from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "users"

urlpatterns = [
    path('login/', views.RateLimitedLoginView.as_view(template_name="users/login.html"), name='login'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='users/password_reset.html',
            email_template_name='users/password_reset_email.txt',
            subject_template_name='users/password_reset_subject.txt',
            from_email=settings.EMAIL_HOST_USER,
            success_url=reverse_lazy('users:password_reset_done'),
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='users/password_reset_done.html',
        ),
        name='password_reset_done',
    ),
    path(
        'password-reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='users/password_reset_confirm.html',
            success_url=reverse_lazy('users:password_reset_complete'),
        ),
        name='password_reset_confirm',
    ),
    path(
        'password-reset/complete/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='users/password_reset_complete.html',
        ),
        name='password_reset_complete',
    ),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('register/', views.register, name='register'),
    path('confirm-email/', views.confirm_email, name='confirm_email'),
    path('send-confirmation/', views.send_confirmation, name='send_confirmation'),
    path('privacy-policy/', views.privacy_policy, name='privacy_policy'),
    path('public-offer/', views.public_offer, name='public_offer'),
    path('feedback/', views.feedback, name='feedback'),
    path("about_me/", views.AboutMeView.as_view(), name="about_me"),
    path("update/", views.ProfileUpdateView.as_view(), name="profile_update"),
    path("", views.UsersListView.as_view(), name="users_list"),
    path("<int:pk>/", views.UserDetailView.as_view(), name="user_detail"),
    path('settings/', views.SettingsPageView.as_view(), name='settings'),
    path(
        'password-change/',
        auth_views.PasswordChangeView.as_view(
            template_name='users/password_change.html',
            success_url=reverse_lazy('users:password_change_done'),
        ),
        name='password_change',
    ),
    path(
        'password-change/done/',
        auth_views.PasswordChangeDoneView.as_view(
            template_name='users/password_change_done.html',
        ),
        name='password_change_done',
    ),
]
