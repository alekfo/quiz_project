from django.shortcuts import render
from django.http import HttpResponse, HttpRequest

def index(request: HttpRequest):

    if request.method == "POST":
        print(request.POST)

    context = {
        "something": "",
    }

    return render(request, "quizzes/quizzes_index.html", context=context)