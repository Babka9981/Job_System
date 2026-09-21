import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import connection

from jobs.models.models import Profile, Resume, Run, Source, Vacancy

ALLOWED_PRIVATE_STATE_ROOTS = ("rocketship-user-state", "source-quotas")


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_database(database_path):
    database = sqlite3.connect(database_path)
    try:
        from django.contrib.auth import get_user_model

        user_table = get_user_model()._meta.db_table
        profile_table = Profile._meta.db_table
        resume_table = Resume._meta.db_table
        source_table = Source._meta.db_table
        run_table = Run._meta.db_table
        vacancy_table = Vacancy._meta.db_table
        checks = {
            "active_owners": database.execute(
                f'SELECT COUNT(*) FROM "{user_table}" WHERE is_active = 1'
            ).fetchone()[0],
            "profiles": database.execute(f'SELECT COUNT(*) FROM "{profile_table}"').fetchone()[0],
            "resumes": database.execute(f'SELECT COUNT(*) FROM "{resume_table}"').fetchone()[0],
            "sources_with_cursor": database.execute(
                f'''SELECT COUNT(*) FROM "{source_table}" WHERE cursor <> '' '''
            ).fetchone()[0],
            "runs": database.execute(f'SELECT COUNT(*) FROM "{run_table}"').fetchone()[0],
        }
        resume_paths = [
            row[0] for row in database.execute(f'SELECT private_path FROM "{resume_table}"')
        ]
        vacancy = database.execute(
            f'SELECT id, user_status, availability, version FROM "{vacancy_table}" ORDER BY id LIMIT 1'
        ).fetchone()
        source = database.execute(
            f'''SELECT id, slug, cursor FROM "{source_table}" WHERE cursor <> '' ORDER BY id LIMIT 1'''
        ).fetchone()
        state = {
            "vacancy": (
                {"id": vacancy[0], "user_status": vacancy[1], "availability": vacancy[2], "version": vacancy[3]}
                if vacancy else None
            ),
            "source": ({"id": source[0], "slug": source[1], "cursor": source[2]} if source else None),
        }
        return checks, resume_paths, state
    finally:
        database.close()


def _resume_archive_path(value, private_root):
    original = Path(value)
    candidate = original if original.is_absolute() else private_root / original
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(private_root):
        raise ValueError(f"resume path outside private root: {value}")
    relative = resolved.relative_to(private_root)
    return "private/" + relative.as_posix()


def _copy_private_file(source, private_root, payload, archive_path):
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"required private file missing or unsafe: {source}")
    resolved = source.resolve(strict=True)
    if not resolved.is_relative_to(private_root):
        raise ValueError(f"private file outside root: {source}")
    target = (payload / archive_path).resolve()
    if not target.is_relative_to(payload / "private"):
        raise ValueError(f"unsafe private archive path: {archive_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resolved, target)


def _copy_allowlisted_private(private_root, payload, resume_paths):
    resume_files = []
    for value in resume_paths:
        if not value:
            continue
        archive_path = _resume_archive_path(value, private_root)
        original = Path(value)
        source = original if original.is_absolute() else private_root / original
        _copy_private_file(source, private_root, payload, archive_path)
        resume_files.append({"database_path": value, "archive_path": archive_path})
    for root_name in ALLOWED_PRIVATE_STATE_ROOTS:
        state_root = private_root / root_name
        if not state_root.exists():
            continue
        if state_root.is_symlink() or not state_root.is_dir():
            raise ValueError(f"allowlisted private state root is unsafe: {root_name}")
        for source in sorted(state_root.rglob("*.json")):
            relative = source.relative_to(private_root)
            _copy_private_file(source, private_root, payload, "private/" + relative.as_posix())
    return resume_files


def _verify_orm(database_path, state):
    script = """
import json
import django
django.setup()
from jobs.models.models import Profile, Source, Vacancy
Profile.objects.exists()
vacancy = Vacancy.objects.values('id', 'user_status', 'availability', 'version').order_by('id').first()
source = Source.objects.exclude(cursor='').values('id', 'slug', 'cursor').order_by('id').first()
print(json.dumps({'vacancy': vacancy, 'source': source}, sort_keys=True))
"""
    environment = dict(os.environ)
    environment["DJANGO_SETTINGS_MODULE"] = "config.settings"
    environment["JOB_DATABASE_PATH"] = str(database_path)
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=settings.BASE_DIR,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("snapshot ORM verification could not run") from error
    if result.returncode != 0:
        raise ValueError("snapshot ORM verification failed")
    try:
        actual = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("snapshot ORM verification returned invalid output") from error
    if actual != state:
        raise ValueError("snapshot ORM state cannot resume")
    return True


def create_snapshot(destination):
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    private_root = Path(settings.PRIVATE_ROOT).resolve()
    with tempfile.TemporaryDirectory(prefix="job-backup-", dir=destination.parent) as work:
        work = Path(work)
        payload = work / "payload"
        payload.mkdir()
        (payload / "private").mkdir()
        snapshot_db = payload / "db.sqlite3"
        connection.ensure_connection()
        target = sqlite3.connect(snapshot_db)
        try:
            connection.connection.backup(target)
        finally:
            target.close()
        checks, resume_paths, state = _inspect_database(snapshot_db)
        resume_files = _copy_allowlisted_private(private_root, payload, resume_paths)
        files = {
            str(path.relative_to(payload)).replace(os.sep, "/"): _sha256(path)
            for path in sorted(payload.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "format": 1,
            "files": files,
            "checks": checks,
            "state": state,
            "resume_files": resume_files,
            "private_allowlist": ["resume_files", *ALLOWED_PRIVATE_STATE_ROOTS],
        }
        (payload / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        staged = destination.with_suffix(destination.suffix + ".tmp")
        try:
            with tarfile.open(staged, "w") as archive:
                archive.add(payload, arcname="payload", recursive=True)
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)
    return manifest


def verify_snapshot(archive_path):
    archive_path = Path(archive_path).resolve()
    with tempfile.TemporaryDirectory(prefix="job-verify-") as work:
        root = Path(work).resolve()
        with tarfile.open(archive_path, "r") as archive:
            for member in archive.getmembers():
                target = (root / member.name).resolve()
                if not target.is_relative_to(root) or member.issym() or member.islnk():
                    raise ValueError("unsafe archive member")
            archive.extractall(root, filter="data")
        payload = root / "payload"
        manifest = json.loads((payload / "manifest.json").read_text(encoding="utf-8"))
        for relative, expected in manifest.get("files", {}).items():
            path = (payload / relative).resolve()
            if not path.is_relative_to(payload) or not path.is_file() or _sha256(path) != expected:
                raise ValueError(f"snapshot checksum failed: {relative}")
        checks, resume_paths, state = _inspect_database(payload / "db.sqlite3")
        if checks != manifest.get("checks"):
            raise ValueError("snapshot database checks do not match manifest")
        if state != manifest.get("state"):
            raise ValueError("snapshot user status or source checkpoint does not match manifest")
        resume_files = manifest.get("resume_files")
        if not isinstance(resume_files, list):
            raise ValueError("snapshot resume manifest missing")
        if sorted(item.get("database_path") for item in resume_files) != sorted(
            value for value in resume_paths if value
        ):
            raise ValueError("snapshot resume paths do not match database")
        for item in resume_files:
            path = (payload / str(item.get("archive_path", ""))).resolve()
            if not path.is_relative_to(payload / "private") or not path.is_file():
                raise ValueError(f"resume file missing: {item.get('database_path', '')}")
        verified = dict(manifest)
        verified["orm_verified"] = _verify_orm(payload / "db.sqlite3", state)
        return verified
