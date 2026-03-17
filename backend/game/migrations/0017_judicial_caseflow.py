from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0016_populate_admin_units'),
    ]

    operations = [
        migrations.CreateModel(
            name='JudicialGenerationState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('PENDING', '待生成'), ('RUNNING', '生成中'), ('READY', '已完成'), ('FAILED', '失败')], default='PENDING', max_length=10)),
                ('total_windows', models.IntegerField(default=0)),
                ('generated_windows', models.IntegerField(default=0)),
                ('last_error', models.TextField(blank=True, default='')),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('game', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='judicial_generation', to='game.gamestate')),
            ],
            options={
                'db_table': 'judicial_generation_states',
            },
        ),
        migrations.CreateModel(
            name='JudicialCaseInstance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('template_case_id', models.CharField(max_length=50)),
                ('county_review_season', models.IntegerField()),
                ('prefect_review_season', models.IntegerField()),
                ('status', models.CharField(choices=[('PENDING_ASSISTANT_REVIEW', '待县丞意见'), ('PENDING_MAGISTRATE_ROUND_1', '待知县一审'), ('RETURNED_FOR_REVIEW', '打回重审'), ('PENDING_MAGISTRATE_ROUND_2', '待知县二审'), ('SUBMITTED_TO_PREFECT', '已上呈知府'), ('DEFERRED_TO_PREFECT', '委托知府裁定'), ('PREFECT_DECIDED', '知府已裁定'), ('WITHDRAWN_THIS_QUARTER', '本季度暂缓')], default='PENDING_MAGISTRATE_ROUND_1', max_length=30)),
                ('local_payload', models.JSONField(default=dict)),
                ('actor_map', models.JSONField(default=dict)),
                ('assistant_rounds', models.JSONField(default=list)),
                ('magistrate_rounds', models.JSONField(default=list)),
                ('submitted_to_prefect', models.BooleanField(default=False)),
                ('submitted_season', models.IntegerField(blank=True, null=True)),
                ('prefect_decision', models.JSONField(blank=True, default=dict)),
                ('debug_meta', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('county_unit', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='judicial_cases', to='game.adminunit')),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='judicial_cases', to='game.gamestate')),
                ('prefect_unit', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='prefecture_judicial_cases', to='game.adminunit')),
            ],
            options={
                'db_table': 'judicial_case_instances',
            },
        ),
        migrations.AddIndex(
            model_name='judicialcaseinstance',
            index=models.Index(fields=['game', 'county_review_season'], name='judicial_ca_game_id_566bd8_idx'),
        ),
        migrations.AddIndex(
            model_name='judicialcaseinstance',
            index=models.Index(fields=['game', 'prefect_review_season'], name='judicial_ca_game_id_a1f2ca_idx'),
        ),
        migrations.AddIndex(
            model_name='judicialcaseinstance',
            index=models.Index(fields=['county_unit', 'status'], name='judicial_ca_county__5ca58c_idx'),
        ),
        migrations.AddIndex(
            model_name='judicialcaseinstance',
            index=models.Index(fields=['prefect_unit', 'status'], name='judicial_ca_prefect_8a4a9b_idx'),
        ),
    ]
