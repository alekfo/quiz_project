from django.urls import path
from . import views

app_name = "gameplay"

urlpatterns = [
    path('quiz/<int:pk>/start/', views.start, name='start'),
]