from django.urls import path
from . import views

app_name = "quizzes"

urlpatterns = [
    path('menu/', views.menu, name='menu'),
    path('<int:pk>/', views.QuizDetailView.as_view(), name='quizzes_details'),
    path('<int:pk>/preview', views.QuizPreviewView.as_view(), name='quizzes_preview'),
    path('', views.QuizListView.as_view(), name='quizzes_list'),
    path('create/', views.QuizCreateView.as_view(), name='quizzes_create'),
    path('<int:pk>/delete/', views.QuizDeleteView.as_view(), name='quizzes_delete'),
    path('<int:pk>/update/', views.QuizUpdateView.as_view(), name='quizzes_update'),
]