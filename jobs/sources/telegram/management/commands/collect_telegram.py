from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from jobs.models.models import Source
from jobs.sources.core.collector import CollectorBusy, collect_sources


class Command(BaseCommand):
    help = "Collect enabled owner Telegram channels only, without LLM or notifications."

    def add_arguments(self, parser):
        parser.add_argument("--source", action="append", help="Enabled source slug; repeat to select channels.")
        parser.add_argument("--max-pages", type=int, default=1, help="History pages per channel (1–20).")

    def handle(self, *args, **options):
        if not 1 <= options["max_pages"] <= 20:
            raise CommandError("max-pages must be between 1 and 20.")
        owner = get_user_model().objects.filter(username=settings.OWNER_USERNAME).first()
        if owner is None:
            raise CommandError("Owner account is not configured.")
        sources = Source.objects.filter(owner=owner, enabled=True, kind="telegram", adapter="telegram")
        if options["source"] is not None:
            sources = sources.filter(slug__in=options["source"])
        slugs = list(sources.values_list("slug", flat=True))
        try:
            report = collect_sources(owner, holder=f"telegram-probe:{uuid4()}", source_slugs=slugs, max_pages=options["max_pages"])
        except CollectorBusy:
            raise CommandError("Telegram collection is busy or ownership was lost.") from None
        self.stdout.write(f"succeeded={report.succeeded} failed={report.failed} skipped={report.skipped} records={report.records}")
        for source in sources.order_by("slug"):
            self.stdout.write(f"{source.slug}: {source.status}")
