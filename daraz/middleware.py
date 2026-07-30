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
