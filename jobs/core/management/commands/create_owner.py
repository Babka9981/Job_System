import os
from getpass import getpass
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    help = "Create the single local owner account (closed registration)."

    def add_arguments(self, parser):
        parser.add_argument("--username", default=settings.OWNER_USERNAME)
        parser.add_argument("--no-input", action="store_true")

    def handle(self, *args, **options):
        users = get_user_model()
        username = options["username"]
        if username != settings.OWNER_USERNAME:
            raise CommandError("--username must match JOB_OWNER_USERNAME")
        if users.objects.filter(username=username).exists():
            self.stdout.write(self.style.WARNING(f"Owner {username} already exists"))
            return
        password = os.environ.get("JOB_OWNER_PASSWORD")
        if not password and not options["no_input"]:
            password = getpass("Owner password: ")
        if not password:
            raise CommandError("Set JOB_OWNER_PASSWORD or run interactively")
        owner = users(username=username)
        try:
            validate_password(password, user=owner)
        except ValidationError as error:
            raise CommandError(f'Password rejected by Django validators: {"; ".join(error.messages)}') from error
        owner.set_password(password)
        owner.save()
        self.stdout.write(self.style.SUCCESS(f"Owner {username} created"))
