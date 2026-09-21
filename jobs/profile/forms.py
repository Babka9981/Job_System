from django import forms
from django.core.exceptions import ValidationError
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jobs.intelligence.config import DEFAULT_OPENAI_MODEL


class StyledForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.HiddenInput, forms.CheckboxInput)):
                field.widget.attrs.setdefault("class", "form-control")


class ResumeUploadForm(StyledForm):
    resume = forms.FileField(label="Резюме PDF или DOCX", help_text="До 10 МБ; файл хранится приватно.")


class ManualResumeTextForm(StyledForm):
    text = forms.CharField(label="Текст резюме", max_length=200000, widget=forms.Textarea(attrs={"rows": 8}), help_text="Разделяйте смысловые блоки пустой строкой.")


class ManualProfileForm(StyledForm):
    version = forms.IntegerField(widget=forms.HiddenInput)
    about = forms.CharField(label="Обо мне", widget=forms.Textarea(attrs={"rows": 5}))
    experience = forms.CharField(label="Опыт", required=False, widget=forms.Textarea(attrs={"rows": 5}), help_text="Один факт на строку.")
    achievements = forms.CharField(label="Достижения", required=False, widget=forms.Textarea(attrs={"rows": 5}))
    cases = forms.CharField(label="Кейсы", required=False, widget=forms.Textarea(attrs={"rows": 5}))
    languages = forms.CharField(label="Языки", required=False, widget=forms.Textarea(attrs={"rows": 3}))


class ProfileSettingsForm(StyledForm):
    version = forms.IntegerField(widget=forms.HiddenInput)
    roles = forms.CharField(label="Роли", widget=forms.Textarea(attrs={"rows": 3}))
    industries = forms.CharField(label="Отрасли", required=False)
    salary_target = forms.DecimalField(label="Целевая оплата", min_value=0, decimal_places=2)
    salary_currency = forms.CharField(label="Валюта", max_length=3, initial="USD")
    salary_period = forms.ChoiceField(label="Период", choices=[("month", "В месяц"), ("year", "В год"), ("hour", "В час")])
    salary_basis = forms.ChoiceField(label="Основа", choices=[("unknown", "Не определено"), ("gross", "Gross"), ("net", "Net"), ("b2b", "B2B")])
    work_modes = forms.MultipleChoiceField(
        label="Формат работы", required=False, widget=forms.SelectMultiple(attrs={"size": 3}),
        choices=[("remote", "Удалённо"), ("relocation", "Релокация"), ("onsite", "Офис")],
    )
    residence_country = forms.CharField(label="Страна проживания", required=False)
    hiring_countries = forms.CharField(label="Допустимые страны найма", required=False)
    timezone = forms.CharField(label="Часовой пояс", required=False, help_text="Например, Europe/Moscow")
    languages = forms.CharField(label="Языки", required=False)
    response_language = forms.ChoiceField(label="Язык отклика", choices=[("ru", "Русский"), ("en", "English")])
    tone = forms.ChoiceField(label="Тон", choices=[("professional", "Профессиональный"), ("warm", "Тёплый"), ("direct", "Прямой")])
    length = forms.ChoiceField(label="Длина", choices=[("short", "Коротко"), ("medium", "Средне"), ("long", "Подробно")])
    emphasis = forms.CharField(label="Акцент отклика", required=False)
    schedule = forms.CharField(label="Расписание", help_text="Время через запятую, например 09:00, 13:00")
    daily_budget_usd = forms.DecimalField(label="Суточный бюджет, USD", min_value=0, decimal_places=4)
    openai_model = forms.RegexField(
        label="Модель OpenAI",
        regex=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$",
        initial=DEFAULT_OPENAI_MODEL,
        error_messages={"invalid": "Укажите корректное имя модели OpenAI."},
    )
    search_provider = forms.ChoiceField(
        label="Поисковый провайдер",
        choices=[("auto", "Автоматически"), ("tavily", "Tavily"), ("brave", "Brave")],
        initial="auto",
    )

    def clean_schedule(self):
        values = [item.strip() for item in self.cleaned_data["schedule"].split(",") if item.strip()]
        for value in values:
            try:
                hours, minutes = map(int, value.split(":"))
            except (ValueError, TypeError) as exc:
                raise ValidationError("Используйте время в формате ЧЧ:ММ.") from exc
            if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
                raise ValidationError("Используйте время в формате ЧЧ:ММ.")
        return values

    def clean_timezone(self):
        value = self.cleaned_data["timezone"].strip()
        if not value:
            return ""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError("Неизвестный часовой пояс. Используйте имя IANA, например Europe/Moscow.") from None
        return value


def split_values(value):
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]
