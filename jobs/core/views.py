import hashlib
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.shortcuts import render
from .forms import OwnerAuthenticationForm
from .access import owner_required

SECTIONS = {
    "vacancies": ("Вакансии", "Здесь появятся найденные вакансии."),
    "profile": ("Мой профиль", "Загрузите резюме и настройте критерии поиска."),
    "sources": ("Источники", "Подключённые источники появятся здесь."),
}

class RateLimitedLoginView(LoginView):
    authentication_form = OwnerAuthenticationForm
    template_name = "registration/login.html"
    max_attempts = 5
    window_seconds = 300

    def throttle_key(self):
        identity = f'{self.request.META.get("REMOTE_ADDR", "unknown")}:{self.request.POST.get("username", "")}'.encode()
        return f'login-attempts:{hashlib.sha256(identity).hexdigest()}'

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST" and cache.get(self.throttle_key(), 0) >= self.max_attempts:
            return render(request, self.template_name, {"form": self.get_form_class()(request=request), "rate_limited": True}, status=429)
        return super().dispatch(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self.throttle_key()
        attempts = cache.get(key, 0) + 1
        cache.set(key, attempts, self.window_seconds)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self.throttle_key())
        return super().form_valid(form)

@owner_required
def section(request, section):
    title, empty_text = SECTIONS[section]
    return render(request, "shared/section.html", {"section": section, "title": title, "empty_text": empty_text})

def permission_denied(request, exception=None):
    return render(request, "shared/403.html", status=403)
