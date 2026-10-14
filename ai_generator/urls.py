from django.urls import path
from . import views

app_name = "ai_generator"

# pk в review/status/save/discard - это pk GenerationRequest
urlpatterns = [
    path('', views.index, name='index'),
    path('series/<int:series_id>', views.index, name='index_for_series'),
    path('review/<int:pk>/', views.review, name='review'),
    path('status/<int:pk>/', views.status, name='status'),
    path('save/<int:pk>/', views.save, name='save'),
    path('discard/<int:pk>/', views.discard, name='discard'),
]
