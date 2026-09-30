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

    def __init__(self, *args, user=None, **kwargs):
        """
        По умолчанию ModelForm строит choices для FK current_series из ВСЕХ QuizSeries,
        т.е. хосту в выпадающем списке показались бы и чужие приватные серии.
        visible_to(user) сужает список до публичных + своих - то же правило видимости,
        что у превью/старта/лайков. Это же ограничение действует и при валидации:
        ModelChoiceField отклонит pk, которого нет в queryset, даже если его
        подставить в POST вручную.
        """
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["current_series"].queryset = QuizSeries.objects.visible_to(user)

class RoomPlayerReadyForm(forms.ModelForm):
    class Meta:
        model = RoomPlayer
        fields = ["is_ready"]
        labels = {
            "is_ready": "Готовность играть",
        }