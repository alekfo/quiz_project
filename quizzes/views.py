from django.shortcuts import render
from django.http import HttpResponse, HttpRequest
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView

from .models import Quiz, Question, AnswerOption

def index(request: HttpRequest):

    if request.method == "POST":
        print(request.POST)

    context = {
        "something": "",
    }

    return render(request, "quizzes/quizzes_index.html", context=context)

class QuizzesDetailView(DetailView):

    queryset = Quiz.objects.select_related("user").prefetch_related("questions__options")