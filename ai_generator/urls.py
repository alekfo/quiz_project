from django.urls import path
from . import views

app_name = "ai_generator"

urlpatterns = [
    path('', views.index, name='index'),
    path('save/', views.save, name='save'),
]