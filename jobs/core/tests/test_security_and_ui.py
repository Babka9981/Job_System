import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    OWNER_USERNAME="owner",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "security-tests"}},
)
class SecurityAndUiTests(TestCase):
    def test_repeated_failed_login_is_rate_limited(self):
        get_user_model().objects.create_user("owner", password="correct")
        for _ in range(5):
            self.client.post(reverse("login"), {"username": "owner", "password": "wrong"})
        response = self.client.post(reverse("login"), {"username": "owner", "password": "wrong"})
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Слишком много попыток", status_code=429)

    def test_create_owner_command_uses_environment_secret(self):
        with patch.dict(os.environ, {"JOB_OWNER_PASSWORD": "fixture-password"}):
            call_command("create_owner", stdout=StringIO())
        owner = get_user_model().objects.get(username="owner")
        self.assertTrue(owner.check_password("fixture-password"))

    def test_create_owner_rejects_weak_password(self):
        with patch.dict(os.environ, {"JOB_OWNER_PASSWORD": "password"}):
            with self.assertRaisesRegex(CommandError, "Password rejected by Django validators"):
                call_command("create_owner", stdout=StringIO())
        self.assertFalse(get_user_model().objects.filter(username="owner").exists())

    def test_create_owner_rejects_username_that_would_not_have_owner_access(self):
        with patch.dict(os.environ, {"JOB_OWNER_PASSWORD": "fixture-password"}):
            with self.assertRaisesRegex(CommandError, "must match JOB_OWNER_USERNAME"):
                call_command("create_owner", username="different", stdout=StringIO())
        self.assertFalse(get_user_model().objects.filter(username="different").exists())

    def test_standard_password_validators_are_enabled(self):
        from django.conf import settings
        self.assertGreaterEqual(len(settings.AUTH_PASSWORD_VALIDATORS), 4)

    def test_shell_exposes_keyboard_and_theme_contracts(self):
        owner = get_user_model().objects.create_user("owner")
        self.client.force_login(owner)
        response = self.client.get(reverse("vacancies"))
        self.assertContains(response, 'href="#main-content"')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, 'aria-controls="primary-navigation"')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, "data-theme-toggle")
        self.assertContains(response, "data-drawer")

    def test_login_form_has_csrf_and_accessible_labels(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, '<label for="id_username">Логин</label>', html=True)
        self.assertContains(response, '<label for="id_password">Пароль</label>', html=True)
