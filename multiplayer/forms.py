from django import forms
from django.db.models import Q

from .models import Room, RoomPlayer
from quizzes.models import QuizSeries

class RoomForm(forms.ModelForm):

    class Meta:
        model = Room
        fields = ["title",]
        labels = {
            "title": "Название комнаты",
        }

class RoomSeriesForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["current_series"]

    # переопределяем получение всех QuizSeries текущего пользователя или все публичные, иначе для хоста в комнате будут и чужие приватные QuizSeries тоже
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["current_series"].queryset = QuizSeries.objects.filter(Q(user=user) | Q(status="public"))

class RoomPlayerReadyForm(forms.ModelForm):
    class Meta:
        model = RoomPlayer
        fields = ["is_ready"]
        labels = {
            "is_ready": "Готовность играть",
        }