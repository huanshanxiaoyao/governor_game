from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0023_user_login_log_session_key'),
    ]

    operations = [
        migrations.CreateModel(
            name='Letter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('player_is_sender', models.BooleanField(default=False)),
                ('player_is_recipient', models.BooleanField(default=False)),
                ('circular_recipient_ids', models.JSONField(blank=True, default=list)),
                ('letter_type', models.CharField(
                    choices=[('OFFICIAL', '公文'), ('PERSONAL', '私信'), ('MEMORIAL', '奏折'),
                             ('INTELLIGENCE', '情报'), ('CIRCULAR', '檄文')],
                    default='OFFICIAL', max_length=20)),
                ('subject', models.CharField(max_length=200)),
                ('body', models.TextField()),
                ('confidentiality', models.CharField(
                    choices=[('PUBLIC', '公开'), ('PERSONAL', '个人'),
                             ('SECRET', '机密'), ('BURN', '焚毁件')],
                    default='PERSONAL', max_length=20)),
                ('sent_month', models.IntegerField()),
                ('delivery_delay', models.IntegerField(default=1)),
                ('delivered_month', models.IntegerField()),
                ('requires_reply', models.BooleanField(default=False)),
                ('is_blocking', models.BooleanField(default=False)),
                ('reply_deadline_month', models.IntegerField(blank=True, null=True)),
                ('reply_options', models.JSONField(blank=True, null=True)),
                ('default_choice_id', models.CharField(blank=True, max_length=50, null=True)),
                ('reply_body', models.TextField(blank=True, default='')),
                ('reply_choice_id', models.CharField(blank=True, default='', max_length=50)),
                ('replied_month', models.IntegerField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[('DRAFT', '草稿'), ('IN_TRANSIT', '传递中'), ('DELIVERED', '已送达'),
                             ('READ', '已读'), ('REPLIED', '已回复'), ('ARCHIVED', '已归档'),
                             ('BURNED', '已焚毁')],
                    default='IN_TRANSIT', max_length=20)),
                ('read_at_month', models.IntegerField(blank=True, null=True)),
                ('llm_generated', models.BooleanField(default=False)),
                ('generation_context', models.JSONField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('game', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='letters', to='game.gamestate')),
                ('parent_letter', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='thread_replies', to='game.letter')),
                ('recipient_agent', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='letters_received', to='game.agent')),
                ('sender_agent', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='letters_sent', to='game.agent')),
            ],
            options={
                'db_table': 'letters',
                'ordering': ['-sent_month', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='letter',
            index=models.Index(fields=['game', 'status'], name='letters_game_status_idx'),
        ),
        migrations.AddIndex(
            model_name='letter',
            index=models.Index(fields=['game', 'delivered_month'],
                               name='letters_game_delivered_idx'),
        ),
        migrations.AddIndex(
            model_name='letter',
            index=models.Index(fields=['game', 'is_blocking', 'reply_deadline_month'],
                               name='letters_blocking_idx'),
        ),
    ]
