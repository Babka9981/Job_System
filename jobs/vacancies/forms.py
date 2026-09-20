from urllib.parse import urlsplit

from django import forms


class ManualVacancyForm(forms.Form):
    url = forms.URLField(label="Ссылка на вакансию", max_length=1000)
    text = forms.CharField(label="Разрешённый текст", required=False, widget=forms.Textarea(attrs={"rows": 8}))
    description_permission = forms.BooleanField(
        label="У меня есть право сохранить и обработать этот текст",
        required=False,
    )
    title = forms.CharField(label="Название", max_length=300, required=False)
    company = forms.CharField(label="Компания", max_length=300, required=False)

    def clean_url(self):
        url = self.cleaned_data["url"]
        if urlsplit(url).scheme.lower() not in {"http", "https"}:
            raise forms.ValidationError("Допустимы только HTTP(S)-ссылки.")
        return url

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("text") and not cleaned.get("description_permission"):
            self.add_error("description_permission", "Подтвердите право обработки вставленного текста.")
        return cleaned
