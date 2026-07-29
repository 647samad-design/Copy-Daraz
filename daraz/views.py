from django.http import HttpResponse
from django.shortcuts import render


def hello_world(request):
    """
    Simple backend view -> returns Hello World (used for API testing)
    """
    return HttpResponse("Hello World! - Copy-Daraz Backend is running.")


def home(request):
    """
    Frontend view -> renders a simple HTML template
    """
    return render(request, "daraz/index.html")
