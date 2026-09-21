import io

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase


class MonitoringCommandWithoutProfileTests(TestCase):
    def test_owner_without_profile_is_honestly_disabled(self):
        get_user_model().objects.create_user("owner", password="test-password")
        output = io.StringIO()

        call_command("run_monitoring", stdout=output)

        self.assertIn("monitoring disabled/not due: monitoring_disabled", output.getvalue())
