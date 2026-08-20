from django.shortcuts import render
from django.http import HttpResponse, HttpRequest, HttpResponseRedirect

def room_create(request: HttpRequest) -> HttpResponse:
    return HttpResponse("тут будет создаваться команата")