from django.urls import path
from . import views

app_name = "gameplay"

urlpatterns = [
    path('quiz/<int:pk>/start/', views.start, name='start'),
    path('session/<int:pk>/play/', views.play, name='play'),
    path('session/<int:pk>/result/', views.result, name='result'),
]