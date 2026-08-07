from django.shortcuts import render
from django.http import HttpResponse, HttpRequest, HttpResponseRedirect

def start(request: HttpRequest, pk: int):

    if request.method == "POST":
        print(request.POST)

    context = {
        "something": "",
    }

    return render(request, "gameplay/play.html", context=context)