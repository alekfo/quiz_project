from django.urls import path
from . import views

app_name = "quizzes"

urlpatterns = [
    path('menu/', views.menu, name='menu'),
    path('<int:pk>/', views.QuizzesDetailView.as_view(), name='quizzes_details'),
    path('', views.QuizzesListView.as_view(), name='quizzes_list'),
]