from django import forms

from .models import Room, RoomPlayer
from quizzes.models import Quiz

class RoomForm(forms.ModelForm):

    class Meta:
        model = Room
        fields = ["title",]
        labels = {
            "title": "Название комнаты",
        }


class RoomQuizForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["current_quiz"]

    #переопределяем получение всех Quiz текущего пользователя, иначе для хоста в комнате будут и чужие квизы тоже
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["current_quiz"].queryset = Quiz.objects.filter(user=user)

class RoomPlayerReadyForm(forms.ModelForm):
    class Meta:
        model = RoomPlayer
        fields = ["is_ready"]
        labels = {
            "is_ready": "Готовность играть",
        }