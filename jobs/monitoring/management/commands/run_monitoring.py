from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from jobs.models.models import Profile
from jobs.monitoring.schedule import schedule_decision
from jobs.monitoring.service import CycleBusy, run_cycle


class Command(BaseCommand):
    help = "Run one due monitoring cycle; invoke frequently from an OS scheduler."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Run without checking a schedule slot.")

    def handle(self, *args, **options):
        owner = get_user_model().objects.filter(username=settings.OWNER_USERNAME).first()
        if owner is None:
            raise CommandError("Owner account is not configured.")
        profile = Profile.objects.filter(owner=owner).first()
        preferences = profile.preferences if profile and isinstance(profile.preferences, dict) else {}
        criteria = profile.criteria if profile and isinstance(profile.criteria, dict) else {}
        if options["force"]:
            decision = schedule_decision(now=timezone.now())
        else:
            decision = schedule_decision(
                now=timezone.now(),
                timezone_name=criteria.get("timezone", ""),
                slots=preferences.get("schedule", ()),
                enabled=preferences.get("schedule_enabled") is True,
            )
            if not decision.enabled or not decision.due:
                self.stdout.write(f"monitoring disabled/not due: {decision.reason}")
                return
        try:
            run = run_cycle(owner, schedule_slot=decision.slot_key if not options["force"] else "")
        except CycleBusy as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"monitoring run {run.pk}: {run.status}"))
