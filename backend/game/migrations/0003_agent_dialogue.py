from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0002_playerprofile'),
    ]

    operations = [
        # Add game FK to Agent (no existing rows, safe default)
        migrations.AddField(
            model_name='agent',
            name='game',
            field=models.ForeignKey(
                default=1,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='agents',
                to='game.gamestate',
            ),
            preserve_default=False,
        ),
        # Add role_title to Agent
        migrations.AddField(
            model_name='agent',
            name='role_title',
            field=models.CharField(default='', help_text='显示称谓 (师爷/知府/地主/耆老/里长)', max_length=50),
            preserve_default=False,
        ),
        # Update role field to use choices
        migrations.AlterField(
            model_name='agent',
            name='role',
            field=models.CharField(
                choices=[('ADVISOR', '师爷'), ('PREFECT', '知府'), ('GENTRY', '士绅'), ('VILLAGER', '村民')],
                help_text='角色',
                max_length=50,
            ),
        ),
        # Add index on (game, role)
        migrations.AddIndex(
            model_name='agent',
            index=models.Index(fields=['game', 'role'], name='agents_game_id_e71a47_idx'),
        ),
        # Create DialogueMessage model
        migrations.CreateModel(
            name='DialogueMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('player', '玩家'), ('agent', 'NPC'), ('system', '系统')], help_text='消息角色', max_length=10)),
                ('content', models.TextField(help_text='消息内容')),
                ('season', models.IntegerField(help_text='对话时的季度')),
                ('metadata', models.JSONField(blank=True, default=dict, help_text='附加数据 (reasoning, attitude_change等)')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('agent', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dialogue_messages', to='game.agent')),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dialogue_messages', to='game.gamestate')),
            ],
            options={
                'db_table': 'dialogue_messages',
                'indexes': [
                    models.Index(fields=['game', 'agent', '-created_at'], name='dialogue_me_game_id_1a2b3c_idx'),
                ],
            },
        ),
    ]

