"""
Migration 0018: PlayerProfile v2

Changes:
- Remove background field
- Add authority (威名) reputation dimension
- Add state_vs_people, central_vs_local, pragmatic_vs_ideal ideology fields
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0017_judicial_caseflow'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='playerprofile',
            name='background',
        ),
        migrations.AddField(
            model_name='playerprofile',
            name='authority',
            field=models.IntegerField(default=20, help_text='威名：必要时强硬、令人敬畏的口碑'),
        ),
        migrations.AddField(
            model_name='playerprofile',
            name='state_vs_people',
            field=models.FloatField(default=0.5, help_text='社稷—黎民：0=优先百姓，1=优先国家指标'),
        ),
        migrations.AddField(
            model_name='playerprofile',
            name='central_vs_local',
            field=models.FloatField(default=0.5, help_text='集权—分权：0=地方自主，1=恭顺中央'),
        ),
        migrations.AddField(
            model_name='playerprofile',
            name='pragmatic_vs_ideal',
            field=models.FloatField(default=0.5, help_text='现实—理想：0=坚守原则，1=务实妥协'),
        ),
    ]
