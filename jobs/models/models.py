from django.conf import settings
from django.db import models
from .storage import temporary_content_storage

class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True

class Profile(TimestampedModel):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="job_profile")
    version = models.PositiveIntegerField(default=1)
    confirmed_version = models.PositiveIntegerField(default=0)
    about = models.TextField(blank=True)
    criteria = models.JSONField(default=dict, blank=True)
    preferences = models.JSONField(default=dict, blank=True)

class Resume(TimestampedModel):
    class ExtractionState(models.TextChoices):
        PENDING = "pending", "Ожидает"
        COMPLETE = "complete", "Извлечено"
        ERROR = "error", "Ошибка"
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="resumes")
    private_path = models.CharField(max_length=500)
    text = models.TextField(blank=True)
    extraction_state = models.CharField(max_length=16, choices=ExtractionState, default=ExtractionState.PENDING)

class ProfileFact(TimestampedModel):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="facts")
    text = models.TextField()
    kind = models.CharField(max_length=64)
    source = models.CharField(max_length=255, blank=True)
    page = models.PositiveIntegerField(null=True, blank=True)
    profile_version = models.PositiveIntegerField()
    confirmed = models.BooleanField(default=False)

class Source(TimestampedModel):
    class Status(models.TextChoices):
        READY = "ready", "Работает"
        LIMITED = "limited", "Ограниченная выдача"
        NEEDS_ACCESS = "needs_access", "Нужны данные доступа"
        NOT_CONFIGURED = "not_configured", "Не настроено"
        ERROR = "error", "Ошибка"
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="job_sources")
    slug = models.SlugField(max_length=100)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=32)
    adapter = models.CharField(max_length=100)
    config = models.JSONField(default=dict, blank=True, help_text="Без секретов")
    status = models.CharField(max_length=32, choices=Status, default=Status.NOT_CONFIGURED)
    enabled = models.BooleanField(default=True)
    last_success = models.DateTimeField(null=True, blank=True)
    cursor = models.TextField(blank=True)
    coverage = models.JSONField(default=dict, blank=True)
    llm_permission = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "slug"], name="unique_source_slug_per_owner")]

class VacancyQuerySet(models.QuerySet):
    def order_by_priority(self):
        priority_rank = models.Case(
            models.When(priority="remote", then=models.Value(0)),
            models.When(priority="relocation", then=models.Value(1)),
            default=models.Value(2),
            output_field=models.PositiveSmallIntegerField(),
        )
        return self.alias(_priority_rank=priority_rank).order_by("_priority_rank")


class Vacancy(TimestampedModel):
    class UserStatus(models.TextChoices):
        NEW="new","Новая"; SAVED="saved","Сохранена"; APPLIED="applied","Откликнулся"; INTERVIEW="interview","Интервью"; CLOSED="closed","Закрыта"
    class Availability(models.TextChoices):
        UNKNOWN="unknown","Неизвестна"; ACTIVE="active","Активна"; REMOVED="removed","Снята"
    class Priority(models.TextChoices):
        REMOTE = "remote", "Удалённая"
        RELOCATION = "relocation", "Релокация"
        OTHER = "other", "Остальное"
    objects = VacancyQuerySet.as_manager()
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vacancies")
    title = models.CharField(max_length=300)
    company = models.CharField(max_length=300)
    company_domain = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    role = models.CharField(max_length=100, blank=True)
    industry = models.CharField(max_length=100, blank=True)
    work_arrangement = models.CharField(max_length=50, blank=True)
    country_restrictions = models.JSONField(default=list, blank=True)
    timezone_restrictions = models.JSONField(default=list, blank=True)
    salary_min = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    salary_max = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    salary_currency = models.CharField(max_length=3, blank=True)
    salary_period = models.CharField(max_length=20, blank=True)
    salary_basis = models.CharField(max_length=50, blank=True)
    salary_component = models.CharField(max_length=100, blank=True)
    salary_fixed = models.JSONField(default=dict, blank=True)
    salary_bonus = models.JSONField(default=dict, blank=True)
    salary_equity_tokens = models.JSONField(default=dict, blank=True)
    salary_original = models.TextField(blank=True)
    language = models.CharField(max_length=20, blank=True)
    contact = models.CharField(max_length=255, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    priority = models.CharField(max_length=16, choices=Priority, default=Priority.OTHER)
    user_status = models.CharField(max_length=16, choices=UserStatus, default=UserStatus.NEW)
    availability = models.CharField(max_length=16, choices=Availability, default=Availability.UNKNOWN)
    hidden = models.BooleanField(default=False)
    user_note = models.TextField(blank=True)
    closing_note = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

class SourceRecord(TimestampedModel):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="records")
    vacancy = models.ForeignKey(Vacancy, on_delete=models.CASCADE, related_name="source_records")
    external_id = models.CharField(max_length=255, blank=True)
    canonical_url = models.URLField(max_length=1000)
    apply_url = models.URLField(max_length=1000, blank=True)
    raw_hash = models.CharField(max_length=128)
    adapter_confirmed_permalink = models.BooleanField(default=False)
    description_permission = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    attribution = models.JSONField(default=dict, blank=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"],
                condition=~models.Q(external_id=""),
                name="uniq_source_extid_nonempty",
            ),
            models.UniqueConstraint(
                fields=["source", "canonical_url"],
                condition=models.Q(external_id=""),
                name="uniq_source_url_no_extid",
            ),
        ]

class TemporarySourceContent(TimestampedModel):
    source_record = models.OneToOneField(SourceRecord, on_delete=models.CASCADE, related_name="temporary_content")
    content = models.FileField(storage=temporary_content_storage, upload_to="source-content/%Y/%m/%d")
    expires_at = models.DateTimeField()

class Research(TimestampedModel):
    class Status(models.TextChoices):
        PENDING="pending","Ожидает"; RUNNING="running","Выполняется"; COMPLETE="complete","Готово"; PARTIAL="partial","Частично"; UNAVAILABLE="unavailable","Недоступно"; NEEDS_DOMAIN="needs-domain","Нужен домен"
    vacancy = models.ForeignKey(Vacancy, on_delete=models.CASCADE, related_name="research")
    company = models.CharField(max_length=300)
    domain = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=100, blank=True)
    coverage = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status, default=Status.PENDING)
    facts = models.JSONField(default=list, blank=True)
    sources = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

class Draft(TimestampedModel):
    class Status(models.TextChoices):
        EDITING="editing","Редактируется"; GENERATED="generated","Сгенерирован"; LIMITED="limited","Ограничен"; ERROR="error","Ошибка"
    vacancy = models.ForeignKey(Vacancy, on_delete=models.CASCADE, related_name="drafts")
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="drafts")
    research = models.ForeignKey(Research, on_delete=models.SET_NULL, null=True, blank=True, related_name="drafts")
    kind = models.CharField(max_length=32)
    text = models.TextField(blank=True)
    profile_version = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status, default=Status.EDITING)
    provenance = models.JSONField(default=dict, blank=True)

class Run(TimestampedModel):
    status = models.CharField(max_length=32, default="pending")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)

class Lease(TimestampedModel):
    name = models.CharField(max_length=100, unique=True)
    holder = models.CharField(max_length=255)
    expires_at = models.DateTimeField()

class UsageReservation(TimestampedModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usage_reservations")
    kind = models.CharField(max_length=50)
    units = models.DecimalField(max_digits=12, decimal_places=4)
    status = models.CharField(max_length=20, default="reserved")

class UsageLedger(TimestampedModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usage_ledger")
    kind = models.CharField(max_length=50)
    units = models.DecimalField(max_digits=12, decimal_places=4)
    cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    metadata = models.JSONField(default=dict, blank=True)

class NotificationOutbox(TimestampedModel):
    class Status(models.TextChoices):
        PENDING="pending","Ожидает"; SENT="sent","Отправлено"; FAILED="failed","Ошибка"; UNCERTAIN="uncertain","Неизвестно"
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="notifications")
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    last_error_code = models.CharField(max_length=100, blank=True)
    last_error_message = models.CharField(max_length=500, blank=True)
