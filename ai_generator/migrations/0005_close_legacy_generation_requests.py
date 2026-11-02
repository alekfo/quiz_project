from django.db import migrations


def close_legacy_requests(apps, schema_editor):
    """
    До перехода на статусы save() не помечал GenerationRequest использованной:
    все старые записи висят в 'completed' (или в 'pending'), хотя с ними давно
    всё сделано. Без этой правки index() считал бы каждую из них "несохранённым
    результатом" и по очереди отправлял бы пользователя на страницу проверки.
    """
    GenerationRequest = apps.get_model("ai_generator", "GenerationRequest")
    GenerationRequest.objects.filter(status="completed").update(status="saved")
    GenerationRequest.objects.filter(status__in=["pending", "in_progress"]).update(status="failed")


class Migration(migrations.Migration):

    dependencies = [
        ('ai_generator', '0004_alter_generationrequest_points_per_correct'),
    ]

    operations = [
        migrations.RunPython(close_legacy_requests, migrations.RunPython.noop),
    ]
