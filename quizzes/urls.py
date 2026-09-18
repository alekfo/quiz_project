from django.urls import path
from . import views

app_name = "quizzes"

urlpatterns = [
    path('menu/', views.menu, name='menu'),
    path('<int:pk>/', views.QuizDetailView.as_view(), name='quizzes_details'),
    path('<int:pk>/preview', views.QuizPreviewView.as_view(), name='quizzes_preview'),
    path('', views.QuizListView.as_view(), name='quizzes_list'),
    path('create/', views.QuizCreateView.as_view(), name='quizzes_create'),
    path('<int:series_id>/round/create/', views.QuizCreateView.as_view(), name='quizzes_create_for_series'),
    path('<int:pk>/delete/', views.QuizDeleteView.as_view(), name='quiz_delete'),
    path('round/<int:pk>/delete/', views.RoundDeleteView.as_view(), name='round_delete'),
    path('round/<int:pk>/update/', views.RoundUpdateView.as_view(), name='round_update'),
]