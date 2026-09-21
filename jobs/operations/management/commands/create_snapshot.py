from django.core.management.base import BaseCommand, CommandError

from jobs.operations.snapshot import create_snapshot


class Command(BaseCommand):
    help = "Create a plaintext snapshot for immediate age encryption."

    def add_arguments(self, parser):
        parser.add_argument("destination")

    def handle(self, *args, **options):
        try:
            manifest = create_snapshot(options["destination"])
        except Exception as error:
            raise CommandError(f"snapshot failed: {error}") from error
        self.stdout.write(self.style.SUCCESS(f"snapshot_ok files={len(manifest['files'])}"))
