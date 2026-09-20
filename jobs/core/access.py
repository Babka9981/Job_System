from functools import wraps
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse

def owner_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f'{reverse("login")}?next={request.get_full_path()}')
        if request.user.username != settings.OWNER_USERNAME:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped
