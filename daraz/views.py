from django.shortcuts import render


def home(request):
    """
    Frontend view -> renders the Daraz-style homepage template
    """
    return render(request, "daraz/index.html")
