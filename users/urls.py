from django.urls import path
from django.contrib.auth import views as auth_views

from . import views

app_name = "users"

urlpatterns = [
    path('login/', views.RateLimitedLoginView.as_view(template_name="users/login.html"), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('register/', views.register, name='register'),
    path('confirm-email/', views.confirm_email, name='confirm_email'),
    path('privacy-policy/', views.privacy_policy, name='privacy_policy'),
    path('public-offer/', views.public_offer, name='public_offer'),
]