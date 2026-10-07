class NoCacheMiddleware:
    """
    Prevents the browser from caching pages (especially the back/forward cache),
    which was causing the navbar to briefly show "logged out" until a manual
    refresh after login, add-to-cart, or checkout redirects.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Cache-Control"] = "no-cache, no-store, must-revalidate, private"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response

# Two-step sign-in enforcement for staff
from django.contrib import messages  # noqa: E402
from django.shortcuts import redirect  # noqa: E402

PROTECTED = ("/manage/", "/admin/")


class StaffTwoFactorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        user = getattr(request, "user", None)
        if user is not None and path.startswith(PROTECTED) and user.is_authenticated and user.is_staff:
            from .models import Profile, SiteSettings
            if SiteSettings.load().require_staff_2fa:
                if not Profile.objects.filter(user=user, totp_enabled=True).exists():
                    messages.info(request, "Please set up two-step sign-in to open the store admin. It takes a minute and protects the store if your password is ever stolen.")
                    return redirect("two_factor_setup")
        return self.get_response(request)
