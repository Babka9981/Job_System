from django.core.management.base import BaseCommand, CommandError

from jobs.operations.snapshot import verify_snapshot


class Command(BaseCommand):
    help = "Verify snapshot checksums and owner/profile/checkpoint/resume invariants."

    def add_arguments(self, parser):
        parser.add_argument("archive")

    def handle(self, *args, **options):
        try:
            manifest = verify_snapshot(options["archive"])
        except Exception as error:
            raise CommandError(f"snapshot verification failed: {error}") from error
        self.stdout.write(self.style.SUCCESS(f"snapshot_verified checks={manifest['checks']}"))
