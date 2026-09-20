from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from jobs.sources.telegram.session import TelegramConfigurationError, TelethonHistoryClient


class Command(BaseCommand):
    help = "Локально авторизовать пользовательскую Telegram-session для чтения источников."

    @staticmethod
    def _phone_reader():
        return input("Telegram phone: ").strip()

    @staticmethod
    def _code_reader():
        return getpass("Telegram login code: ").strip()

    @staticmethod
    def _password_reader():
        return getpass("Telegram 2FA password: ")

    def handle(self, *args, **options):
        client = None
        authorized = False
        try:
            client = TelethonHistoryClient.from_environment()
            client.connect()
            authorized = client.authorize_interactive(
                phone_reader=self._phone_reader,
                code_reader=self._code_reader,
                password_reader=self._password_reader,
            )
        except TelegramConfigurationError as exc:
            raise CommandError(exc.safe_message) from None
        except (EOFError, KeyboardInterrupt):
            raise CommandError("Локальная авторизация Telegram отменена.") from None
        except Exception:
            raise CommandError("Не удалось завершить локальную авторизацию Telegram.") from None
        finally:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass
        if not authorized:
            raise CommandError("Telegram не подтвердил пользовательскую авторизацию.")
        self.stdout.write(self.style.SUCCESS("Telegram user-session авторизована локально."))
