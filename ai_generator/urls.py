from django.urls import path
from . import views

app_name = "ai_generator"

urlpatterns = [
    path('', views.index, name='index'),
    path('series/<int:series_id>', views.index, name='index_for_series'),
    path('save/', views.save, name='save'),
]