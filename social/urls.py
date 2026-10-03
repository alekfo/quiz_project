from django.urls import path
from . import views

app_name = "social"

urlpatterns = [
    path('users/<int:user_id>/follow', views.follow, name='follow'),
    path('users/<int:user_id>/unfollow', views.unfollow, name='unfollow'),
    path('series/<int:series_id>/like', views.create_like, name='create_like'),
    path('series/<int:series_id>/unlike', views.delete_like, name='delete_like'),
    path('series/<int:series_id>/save', views.save_series, name='save_series'),
    path('series/<int:series_id>/unsave', views.unsave_series, name='unsave_series'),
]