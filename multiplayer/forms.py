from django import forms

from .models import Room, RoomPlayer

class RoomForm(forms.ModelForm):

    class Meta:
        model = Room
        fields = ["title",]
        labels = {
            "title": "Название комнаты",
        }