"""Gives workspaces created before Phase 2 the same default pipeline and lost reasons new ones get."""

from django.db import migrations

STAGES = [("New lead", 10, 7), ("Contacted", 20, 7), ("Proposal sent", 50, 10), ("Negotiation", 75, 10)]
LOST_REASONS = ["Price too high", "Chose a competitor", "No budget", "No response", "Not a fit"]


def forwards(apps, schema_editor):
    Organization = apps.get_model("orgs", "Organization")
    Pipeline = apps.get_model("pipeline", "Pipeline")
    Stage = apps.get_model("pipeline", "Stage")
    LostReason = apps.get_model("pipeline", "LostReason")
    for org in Organization.objects.all():
        if not Pipeline.objects.filter(org=org).exists():
            pipeline = Pipeline.objects.create(org=org, name="Sales pipeline", is_default=True)
            for i, (name, probability, rot) in enumerate(STAGES):
                Stage.objects.create(org=org, pipeline=pipeline, name=name, probability=probability, rot_days=rot, position=i)
        if not LostReason.objects.filter(org=org).exists():
            for i, name in enumerate(LOST_REASONS):
                LostReason.objects.create(org=org, name=name, position=i)


class Migration(migrations.Migration):
    dependencies = [
        ("pipeline", "0001_initial"),
        ("orgs", "0001_initial"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
