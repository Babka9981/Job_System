import io
import tarfile
import tempfile
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from jobs.models.models import Profile, Resume, Source, SourceRecord, TemporarySourceContent, Vacancy
from jobs.operations.snapshot import create_snapshot, verify_snapshot


class HealthTests(TestCase):
    def test_health_is_public_minimal_and_no_store(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")


class CleanupTests(TestCase):
    def test_cleanup_deletes_only_expired_temporary_rows_and_files(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(TEMPORARY_ROOT=Path(directory)):
            owner = get_user_model().objects.create_user(username="owner")
            source = Source.objects.create(owner=owner, slug="source", name="Source", kind="site", adapter="fixture")
            vacancy = Vacancy.objects.create(owner=owner, title="Role", company="Company")
            old_record = SourceRecord.objects.create(source=source, vacancy=vacancy, external_id="old", raw_hash="a")
            fresh_record = SourceRecord.objects.create(source=source, vacancy=vacancy, external_id="fresh", raw_hash="b")
            old = TemporarySourceContent.objects.create(source_record=old_record, expires_at=timezone.now() - timedelta(seconds=1))
            old.content.save("old.txt", ContentFile(b"expired"))
            fresh = TemporarySourceContent.objects.create(source_record=fresh_record, expires_at=timezone.now() + timedelta(hours=1))
            fresh.content.save("fresh.txt", ContentFile(b"fresh"))
            old_path = Path(old.content.path)
            fresh_path = Path(fresh.content.path)

            call_command("cleanup_expired", stdout=io.StringIO())

            self.assertFalse(TemporarySourceContent.objects.filter(pk=old.pk).exists())
            self.assertFalse(old_path.exists())
            self.assertTrue(TemporarySourceContent.objects.filter(pk=fresh.pk).exists())
            self.assertTrue(fresh_path.exists())


class SnapshotTests(TransactionTestCase):
    databases = "__all__"

    def test_snapshot_includes_resume_and_excludes_sessions_secrets_and_cache(self):
        with tempfile.TemporaryDirectory() as private_dir, tempfile.TemporaryDirectory() as output_dir:
            private = Path(private_dir)
            (private / "resumes").mkdir()
            (private / "resumes" / "cv.pdf").write_bytes(b"cv")
            (private / "reader.session").write_bytes(b"session-secret")
            (private / "secrets").mkdir()
            (private / "secrets" / "token.txt").write_text("secret", encoding="utf-8")
            (private / "matching-cache").mkdir()
            (private / "matching-cache" / "cache").write_text("recreatable", encoding="utf-8")
            (private / "innocent-but-unapproved.txt").write_text("not-allowlisted", encoding="utf-8")
            (private / "db.sqlite3-wal").write_text("wal", encoding="utf-8")
            (private / "db.sqlite3-shm").write_text("shm", encoding="utf-8")
            (private / "nested").mkdir()
            (private / "nested" / "another.session").write_text("session", encoding="utf-8")
            (private / "rocketship-user-state" / "1").mkdir(parents=True)
            (private / "rocketship-user-state" / "1" / "saved.json").write_text(
                '{"status":"saved","note":"mine"}', encoding="utf-8"
            )
            (private / "source-quotas" / "remote-rocketship").mkdir(parents=True)
            (private / "source-quotas" / "remote-rocketship" / "2026-09-21.json").write_text(
                '{"date":"2026-09-21","requests":1}', encoding="utf-8"
            )
            owner = get_user_model().objects.create_user(username="owner", is_active=True)
            profile = Profile.objects.create(owner=owner)
            Resume.objects.create(profile=profile, private_path=str(private / "resumes" / "cv.pdf"))
            source = Source.objects.create(owner=owner, slug="checkpoint", name="Source", kind="site", adapter="fixture", cursor="page-2")
            vacancy = Vacancy.objects.create(
                owner=owner,
                title="Saved role",
                company="Company",
                user_status=Vacancy.UserStatus.SAVED,
            )
            archive = Path(output_dir) / "snapshot.tar"

            with override_settings(PRIVATE_ROOT=private):
                manifest = create_snapshot(archive)
                verified = verify_snapshot(archive)

            self.assertEqual(verified["checks"], manifest["checks"])
            with tarfile.open(archive, "r") as bundle:
                names = {member.name for member in bundle.getmembers() if member.isfile()}
            self.assertIn("payload/private/resumes/cv.pdf", names)
            self.assertIn("payload/private/rocketship-user-state/1/saved.json", names)
            self.assertIn("payload/private/source-quotas/remote-rocketship/2026-09-21.json", names)
            self.assertEqual(
                {name for name in names if name.startswith("payload/private/") and not name.endswith("/")},
                {
                    "payload/private/resumes/cv.pdf",
                    "payload/private/rocketship-user-state/1/saved.json",
                    "payload/private/source-quotas/remote-rocketship/2026-09-21.json",
                },
            )
            self.assertEqual(verified["state"]["vacancy"]["id"], vacancy.pk)
            self.assertEqual(verified["state"]["vacancy"]["user_status"], Vacancy.UserStatus.SAVED)
            self.assertEqual(verified["state"]["source"]["id"], source.pk)
            self.assertEqual(verified["state"]["source"]["cursor"], "page-2")
            self.assertTrue(verified["orm_verified"])

    def test_verify_rejects_archive_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.tar"
            with tarfile.open(archive, "w") as bundle:
                member = tarfile.TarInfo("../outside")
                member.size = 1
                bundle.addfile(member, io.BytesIO(b"x"))
            with self.assertRaisesRegex(ValueError, "unsafe archive member"):
                verify_snapshot(archive)

    def test_verify_rejects_unexpected_file_not_declared_by_manifest(self):
        with tempfile.TemporaryDirectory() as private_dir, tempfile.TemporaryDirectory() as output_dir:
            archive = Path(output_dir) / "snapshot.tar"
            poisoned = Path(output_dir) / "poisoned.tar"
            with override_settings(PRIVATE_ROOT=Path(private_dir)):
                create_snapshot(archive)
            with tarfile.open(archive, "r") as source, tarfile.open(poisoned, "w") as target:
                for member in source.getmembers():
                    extracted = source.extractfile(member) if member.isfile() else None
                    target.addfile(member, extracted)
                secret = b"credential"
                member = tarfile.TarInfo("payload/private/unexpected-token.txt")
                member.size = len(secret)
                target.addfile(member, io.BytesIO(secret))
            with self.assertRaisesRegex(ValueError, "unexpected snapshot file"):
                verify_snapshot(poisoned)

    def test_verify_rejects_nested_manifest_named_file(self):
        with tempfile.TemporaryDirectory() as private_dir, tempfile.TemporaryDirectory() as output_dir:
            archive = Path(output_dir) / "snapshot.tar"
            poisoned = Path(output_dir) / "nested-manifest.tar"
            with override_settings(PRIVATE_ROOT=Path(private_dir)):
                create_snapshot(archive)
            with tarfile.open(archive, "r") as source, tarfile.open(poisoned, "w") as target:
                for member in source.getmembers():
                    extracted = source.extractfile(member) if member.isfile() else None
                    target.addfile(member, extracted)
                body = b"credential-like-data"
                member = tarfile.TarInfo("payload/private/nested/manifest.json")
                member.size = len(body)
                target.addfile(member, io.BytesIO(body))
            with self.assertRaisesRegex(ValueError, "unexpected snapshot file"):
                verify_snapshot(poisoned)

    def test_verify_rejects_unknown_snapshot_format(self):
        with tempfile.TemporaryDirectory() as private_dir, tempfile.TemporaryDirectory() as output_dir:
            archive = Path(output_dir) / "snapshot.tar"
            changed = Path(output_dir) / "format.tar"
            with override_settings(PRIVATE_ROOT=Path(private_dir)):
                create_snapshot(archive)
            with tarfile.open(archive, "r") as source, tarfile.open(changed, "w") as target:
                for member in source.getmembers():
                    if member.name == "payload/manifest.json":
                        manifest = __import__("json").loads(source.extractfile(member).read())
                        manifest["format"] = 999
                        body = __import__("json").dumps(manifest).encode()
                        replacement = tarfile.TarInfo(member.name)
                        replacement.size = len(body)
                        target.addfile(replacement, io.BytesIO(body))
                    else:
                        extracted = source.extractfile(member) if member.isfile() else None
                        target.addfile(member, extracted)
            with self.assertRaisesRegex(ValueError, "unsupported snapshot format"):
                verify_snapshot(changed)

    def test_empty_private_snapshot_round_trips_with_private_directory(self):
        with tempfile.TemporaryDirectory() as private_dir, tempfile.TemporaryDirectory() as output_dir:
            private = Path(private_dir)
            archive = Path(output_dir) / "empty-private.tar"
            with override_settings(PRIVATE_ROOT=private):
                create_snapshot(archive)
                verified = verify_snapshot(archive)
            self.assertTrue(verified["orm_verified"])
            with tarfile.open(archive, "r") as bundle:
                private_member = bundle.getmember("payload/private")
            self.assertTrue(private_member.isdir())


class DeploymentContractTests(SimpleTestCase):
    root = Path(__file__).resolve().parents[3]

    def read(self, relative):
        return (self.root / relative).read_text(encoding="utf-8")

    def test_default_openai_setup_has_public_model_and_known_price(self):
        env_example = self.read(".env.example")
        wizard = self.read("deployment/setup-wizard.sh")
        price_json = '{"gpt-5-mini":{"input_per_million":"0.25","cached_input_per_million":"0.025","output_per_million":"2.00"}}'

        self.assertIn("OPENAI_MODEL=gpt-5-mini", env_example)
        self.assertIn(f"OPENAI_PRICES_JSON={price_json}", env_example)
        self.assertIn("JOB_PROFILE_MAX_OUTPUT_TOKENS=8000", env_example)
        self.assertIn('write_env OPENAI_MODEL "$OPENAI_MODEL"', wizard)
        self.assertIn(f'OPENAI_PRICES_JSON=\'{price_json}\'', wizard)
        self.assertIn('write_env OPENAI_PRICES_JSON "$OPENAI_PRICES_JSON"', wizard)
        self.assertIn('ask OPENAI_PRICES_JSON "OPENAI_PRICES_JSON for $OPENAI_MODEL:"', wizard)
        self.assertIn('OPENAI_PRICES_JSON is required for a custom model', wizard)
        self.assertIn("exit 1", wizard)

    def test_dockerignore_recursively_excludes_runtime_and_secrets(self):
        value = self.read(".dockerignore")
        for pattern in (
            "**/.env",
            "**/runtime/**",
            "**/private/**",
            "**/temporary/**",
            "**/secrets/**",
            "**/*.session",
            "**/*-wal",
            "**/*-shm",
        ):
            self.assertIn(pattern, value)

    def test_immutable_image_tag_and_systemd_release_environment(self):
        compose = self.read("deployment/compose.yaml")
        self.assertIn("job-system:${JOB_IMAGE_TAG:?", compose)
        for unit in (
            "job-system-monitor.service",
            "job-system-cleanup.service",
            "job-system-backup.service",
        ):
            value = self.read(f"deployment/systemd/{unit}")
            self.assertIn("EnvironmentFile=/srv/job-system/deployment/release.env", value)

    def test_runbook_passes_owner_secret_and_quiesces_restore(self):
        runbook = self.read("docs/operations/README.md")
        restore = self.read("deployment/restore.sh")
        self.assertIn("-e JOB_OWNER_PASSWORD", runbook)
        self.assertIn("unset JOB_OWNER_PASSWORD", runbook)
        self.assertIn("systemctl disable --now", restore)
        self.assertIn("job-system-monitor.timer", restore)
        for service in ("job-system-monitor.service", "job-system-cleanup.service", "job-system-backup.service"):
            self.assertIn(service, restore)

    def test_restore_is_fail_fast_and_checks_every_writer_before_swap(self):
        script = self.read("deployment/restore.sh")
        self.assertTrue(script.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail\n"))
        marker = script.index("# SWAP STARTS HERE")
        self.assertLess(script.index("assert_writers_inactive"), marker)
        self.assertLess(script.rindex("assert_writers_inactive", 0, marker), marker)
        for unit in (
            "job-system-monitor.timer",
            "job-system-cleanup.timer",
            "job-system-backup.timer",
            "job-system-monitor.service",
            "job-system-cleanup.service",
            "job-system-backup.service",
        ):
            self.assertIn(unit, script[:marker])
        self.assertNotIn("mv \"$data_dir\"", script[:marker])
        self.assertIn("bash ./restore.sh", self.read("docs/operations/README.md"))

    def test_deploy_sync_preserves_production_environment_and_release_tag(self):
        runbook = self.read("docs/operations/README.md")
        rsync = next(line for line in runbook.splitlines() if "rsync -a --delete" in line)
        self.assertIn("--exclude /deployment/.env", rsync)
        self.assertIn("--exclude /deployment/release.env", rsync)
        self.assertIn("--exclude /deployment/runtime/**", rsync)

    def test_restore_rollback_tracks_each_completed_move(self):
        script = self.read("deployment/restore.sh")
        for flag in (
            "data_backed_up=0",
            "private_backed_up=0",
            "data_installed=0",
            "private_installed=0",
            '[[ "$data_installed" -eq 1 ]]',
            '[[ "$private_installed" -eq 1 ]]',
            '[[ "$data_backed_up" -eq 1 ]]',
            '[[ "$private_backed_up" -eq 1 ]]',
        ):
            self.assertIn(flag, script)
