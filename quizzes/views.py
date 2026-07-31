from django.shortcuts import render
from django.http import HttpResponse, HttpRequest
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin

from .models import Quiz, Question, AnswerOption

def menu(request: HttpRequest):

    if request.method == "POST":
        print(request.POST)

    context = {
        "something": "",
    }

    return render(request, "quizzes/quizzes_menu.html", context=context)

class QuizzesDetailView(LoginRequiredMixin, DetailView):
    queryset = Quiz.objects.select_related("user").prefetch_related("questions__options")

class QuizzesListView(LoginRequiredMixin, ListView):

    def get_queryset(self):
        return (
            Quiz.objects
            .prefetch_related("questions__options")
            .filter(user=self.request.user)
        )
