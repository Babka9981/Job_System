from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(OWNER_USERNAME="owner")
class AuthenticationBoundaryTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_user("owner", password="correct horse")
        self.other = users.objects.create_user("other", password="correct horse")

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("vacancies"))
        self.assertRedirects(response, f'{reverse("login")}?next={reverse("vacancies")}')

    def test_owner_can_login_and_reach_all_sections(self):
        self.assertTrue(self.client.login(username="owner", password="correct horse"))
        for name in ("vacancies", "profile", "sources"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_non_owner_gets_custom_403(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("vacancies"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Нет доступа", status_code=403)

    def test_logout_rejects_get_and_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.get(reverse("logout")).status_code, 405)
        self.assertEqual(csrf_client.post(reverse("logout")).status_code, 403)

    def test_registration_route_does_not_exist(self):
        self.assertEqual(self.client.get("/accounts/signup/").status_code, 404)
