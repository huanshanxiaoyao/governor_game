from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0014_magistrate_fields'),
    ]

    operations = [
        # 1. 创建 admin_units 表
        migrations.CreateModel(
            name='AdminUnit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('unit_type', models.CharField(
                    choices=[
                        ('COUNTY', '县/州'),
                        ('PREFECTURE', '府'),
                        ('PROVINCE', '省'),
                        ('EMPIRE', '朝廷'),
                    ],
                    max_length=15,
                )),
                ('unit_data', models.JSONField(default=dict)),
                ('is_player_controlled', models.BooleanField(default=False)),
                ('game', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='admin_units',
                    to='game.gamestate',
                )),
                ('parent', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='children',
                    to='game.adminunit',
                )),
                ('ai_agent', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='governed_units',
                    to='game.agent',
                )),
            ],
            options={
                'db_table': 'admin_units',
            },
        ),
        migrations.AddIndex(
            model_name='adminunit',
            index=models.Index(fields=['game', 'unit_type'], name='admin_units_game_unit_idx'),
        ),
        migrations.AddIndex(
            model_name='adminunit',
            index=models.Index(fields=['parent'], name='admin_units_parent_idx'),
        ),
        # 2. 在 game_states 上新增 player_role 和 player_unit
        migrations.AddField(
            model_name='gamestate',
            name='player_role',
            field=models.CharField(
                choices=[
                    ('COUNTY_MAGISTRATE', '知县'),
                    ('PREFECT', '知府'),
                    ('GOVERNOR', '巡抚'),
                    ('CABINET', '内阁'),
                ],
                default='COUNTY_MAGISTRATE',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='gamestate',
            name='player_unit',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='player_game',
                to='game.adminunit',
            ),
        ),
    ]
