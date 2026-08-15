"""
Lightweight rate limiting for sensitive endpoints (login, signup, checkout,
password reset) - no extra dependency, just Django's cache framework.

Note: this uses Django's default LocMemCache, which is per-process memory.
That's fine for a single-server deployment (the common case for a project
this size), but if 19Bees is ever deployed behind multiple app server
processes/machines without a shared cache (e.g. Redis), each process would
track its own counters independently, meaning the *effective* limit becomes
rate x number_of_processes. Worth revisiting with a Redis cache backend if/
when that happens - the decorator itself wouldn't need to change.
"""
from functools import wraps

from django.core.cache import cache
from django.http import HttpResponse
from django.contrib import messages
from django.shortcuts import redirect


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def ratelimit(key_prefix, rate_limit=5, window_seconds=60, redirect_to=None, message=None, methods=("POST",)):
    """
    Limits a view to `rate_limit` requests per `window_seconds` per client IP.
    Only counts/enforces on the given HTTP methods (default: POST only) -
    e.g. for a login view, this limits login *attempts*, not just loading
    the login page, which would otherwise lock out anyone who reloads it
    a few times.

    - key_prefix: a short unique name for this endpoint (e.g. 'login').
    - On exceeding the limit: redirects back with an error message if
      `redirect_to` is given, otherwise returns a plain 429 response.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                return view_func(request, *args, **kwargs)
            ip = _client_ip(request)
            cache_key = f"ratelimit:{key_prefix}:{ip}"
            count = cache.get(cache_key, 0)
            if count >= rate_limit:
                if redirect_to:
                    messages.error(
                        request,
                        message or "Too many attempts. Please wait a minute and try again.",
                    )
                    return redirect(redirect_to)
                return HttpResponse("Too many requests. Please slow down and try again shortly.", status=429)
            cache.set(cache_key, count + 1, timeout=window_seconds)
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator
