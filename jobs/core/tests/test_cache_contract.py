from pathlib import Path
import os
from unittest.mock import patch

from django.conf import settings
from django.core.cache import caches
from django.test import SimpleTestCase, override_settings
from config.settings import positive_env_int


class MatchingCacheContractTests(SimpleTestCase):
    def test_default_cache_is_durable_and_private(self):
        config = settings.CACHES["default"]
        location = Path(config["LOCATION"]).resolve()
        private_root = Path(settings.PRIVATE_ROOT).resolve()
        temporary_root = Path(settings.TEMPORARY_ROOT).resolve()

        self.assertEqual(config["BACKEND"], "django.core.cache.backends.filebased.FileBasedCache")
        self.assertTrue(location.is_relative_to(private_root))
        self.assertFalse(location.is_relative_to(temporary_root))
        for public_root in settings.STATICFILES_DIRS:
            self.assertFalse(location.is_relative_to(Path(public_root).resolve()))
        self.assertIsInstance(config["TIMEOUT"], int)
        self.assertGreater(config["TIMEOUT"], 0)

    def test_invalid_or_non_positive_timeout_falls_back_to_safe_default(self):
        for value in ("invalid", "0", "-10"):
            with self.subTest(value=value), patch.dict(os.environ, {"JOB_MATCH_CACHE_TIMEOUT_SECONDS": value}):
                self.assertEqual(positive_env_int("JOB_MATCH_CACHE_TIMEOUT_SECONDS", 86400), 86400)

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "cache-contract-test"}})
    def test_tests_can_override_cache_backend(self):
        self.assertEqual(caches["default"].__class__.__name__, "LocMemCache")
