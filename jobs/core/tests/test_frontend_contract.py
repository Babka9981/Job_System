import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class FrontendContractTests(SimpleTestCase):
    def test_drawer_behavior_regressions(self):
        result = subprocess.run(
            ["node", "--test", "jobs/static/ui/tests/drawer.test.cjs"],
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dark_primary_button_contrast_is_wcag_aa(self):
        css = (Path(settings.BASE_DIR) / "jobs/static/ui/tokens.css").read_text(encoding="utf-8")
        dark = re.search(r':root\[data-theme=dark\]\{([^}]*)\}', css).group(1)
        primary = re.search(r'--primary:(#[0-9a-fA-F]{6})', dark).group(1)
        foreground = re.search(r'--text-on-primary:(#[0-9a-fA-F]{6})', dark).group(1)
        def luminance(color):
            values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            channels = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in values]
            return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]
        lighter, darker = sorted((luminance(primary), luminance(foreground)), reverse=True)
        self.assertGreaterEqual((lighter + .05) / (darker + .05), 4.5)

    def test_inter_is_self_hosted(self):
        css = (Path(settings.BASE_DIR) / "jobs/static/ui/fonts.css").read_text(encoding="utf-8")
        base = (Path(settings.BASE_DIR) / "jobs/templates/shared/base.html").read_text(encoding="utf-8")
        self.assertIn('@font-face', css)
        self.assertIn('fonts/Inter.var.ttf', css)
        self.assertNotRegex(css, r'https?://')
        self.assertIn("ui/fonts.css", base)
        self.assertTrue((Path(settings.BASE_DIR) / "jobs/static/ui/fonts/Inter.var.ttf").is_file())
