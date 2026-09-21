from django.db import migrations, models


def deduplicate_drafts(apps, schema_editor):
    Draft = apps.get_model("models", "Draft")
    duplicates = (
        Draft.objects.values("vacancy_id", "kind")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
        .order_by("vacancy_id", "kind")
    )
    for group in duplicates.iterator():
        rows = Draft.objects.filter(
            vacancy_id=group["vacancy_id"], kind=group["kind"],
        ).order_by("-updated_at", "-pk")
        keep = rows.first()
        rows.exclude(pk=keep.pk).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("models", "0002_vacancy_handoff_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="draft",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.RunPython(deduplicate_drafts, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="draft",
            constraint=models.UniqueConstraint(
                fields=("vacancy", "kind"),
                name="unique_draft_kind_per_vacancy",
            ),
        ),
    ]
