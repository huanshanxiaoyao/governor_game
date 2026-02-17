# Generated manually for Promise model

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0006_eventlog_category_eventlog_data_eventlog_description_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Promise',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('promise_type', models.CharField(choices=[('LOWER_TAX', '降低税率'), ('BUILD_SCHOOL', '资助村塾'), ('BUILD_IRRIGATION', '修建水利'), ('RELIEF', '赈灾救济'), ('HIRE_BAILIFFS', '增设衙役'), ('RECLAIM_LAND', '开垦荒地'), ('REPAIR_ROADS', '修缮道路'), ('BUILD_GRANARY', '开设义仓'), ('OTHER', '其他')], help_text='承诺类型', max_length=20)),
                ('description', models.TextField(help_text='人类可读描述')),
                ('status', models.CharField(choices=[('PENDING', '待履行'), ('FULFILLED', '已履行'), ('BROKEN', '已违约')], default='PENDING', max_length=10)),
                ('season_made', models.IntegerField(help_text='承诺时的季度')),
                ('deadline_season', models.IntegerField(help_text='履约截止季度')),
                ('context', models.JSONField(blank=True, default=dict, help_text='承诺上下文参数')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='promises', to='game.gamestate')),
                ('agent', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='promises', to='game.agent')),
                ('negotiation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='promises', to='game.negotiationsession')),
            ],
            options={
                'db_table': 'promises',
            },
        ),
        migrations.AddIndex(
            model_name='promise',
            index=models.Index(fields=['game', 'status'], name='promises_game_id_status_idx'),
        ),
        migrations.AddIndex(
            model_name='promise',
            index=models.Index(fields=['game', 'agent'], name='promises_game_id_agent_idx'),
        ),
        # Update EventLog category choices (add PROMISE)
        migrations.AlterField(
            model_name='eventlog',
            name='category',
            field=models.CharField(
                choices=[('SYSTEM', '系统'), ('INVESTMENT', '投资'), ('TAX', '税率'), ('NEGOTIATION', '谈判'), ('DISASTER', '灾害'), ('SETTLEMENT', '结算'), ('ANNEXATION', '兼并'), ('PROMISE', '承诺')],
                default='SYSTEM', help_text='事件分类', max_length=20,
            ),
        ),
    ]
