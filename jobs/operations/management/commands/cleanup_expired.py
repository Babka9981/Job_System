from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from jobs.models.models import TemporarySourceContent
from jobs.sources.keyed.remote_rocketship import TemporaryRocketshipStore


class Command(BaseCommand):
    help = "Delete expired licensed temporary content before reads and backups."

    def handle(self, *args, **options):
        now = timezone.now()
        files = []
        with transaction.atomic():
            expired = list(
                TemporarySourceContent.objects.select_for_update()
                .filter(expires_at__lte=now)
                .order_by("pk")
            )
            for item in expired:
                if item.content.name:
                    files.append((item.content.storage, item.content.name))
            deleted, _ = TemporarySourceContent.objects.filter(
                pk__in=[item.pk for item in expired]
            ).delete()
        for storage, name in files:
            storage.delete(name)
        report = TemporaryRocketshipStore().purge_all()
        self.stdout.write(
            self.style.SUCCESS(
                f"expired_rows={deleted} rocketship_expired={report.expired} "
                f"rocketship_orphans={report.orphans}"
            )
        )
