from django.test import SimpleTestCase
from django.urls import resolve, reverse


class FeatureDispatchTests(SimpleTestCase):
    def test_vacancy_root_is_delegated_with_compatible_name(self):
        match = resolve("/")
        self.assertEqual(reverse("vacancies"), "/")
        self.assertEqual(match.url_name, "vacancies")
        self.assertEqual(match.func.__module__, "jobs.vacancies.views")

    def test_profile_root_is_delegated_with_compatible_name(self):
        match = resolve("/profile/")
        self.assertEqual(reverse("profile"), "/profile/")
        self.assertEqual(match.url_name, "profile")
        self.assertEqual(match.func.__module__, "jobs.profile.views")

    def test_sources_root_is_delegated_with_compatible_name(self):
        match = resolve("/sources/")
        self.assertEqual(reverse("sources"), "/sources/")
        self.assertEqual(match.url_name, "sources")
        self.assertEqual(match.func.__module__, "jobs.sources.core.views")

    def test_telegram_sources_are_delegated_and_keep_owner_auth_boundary(self):
        match = resolve("/sources/telegram/")
        self.assertEqual(reverse("manage"), "/sources/telegram/")
        self.assertEqual(match.url_name, "manage")
        self.assertEqual(match.func.__module__, "jobs.sources.telegram.views")

        response = self.client.get(reverse("manage"))
        self.assertRedirects(response, f'{reverse("login")}?next={reverse("manage")}')
