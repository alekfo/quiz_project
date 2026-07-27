from django.urls import path
from . import views

app_name = "quizzes"

urlpatterns = [
    path('', views.index, name='index'),
    path('details/<int:pk>/', views.QuizzesDetailView.as_view(), name='quizzes_details'),
]