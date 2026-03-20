from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0019_neighbor_county_attributes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PlayerFeedback',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content', models.TextField(help_text='玩家反馈内容')),
                ('sent_to_feishu', models.BooleanField(default=False, help_text='是否已推送到飞书')),
                ('feishu_error', models.CharField(blank=True, default='', help_text='飞书发送失败原因', max_length=300)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feedbacks', to='game.gamestate')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='game_feedbacks', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'player_feedbacks',
            },
        ),
        migrations.AddIndex(
            model_name='playerfeedback',
            index=models.Index(fields=['user', '-created_at'], name='player_feed_user_id_03431d_idx'),
        ),
        migrations.AddIndex(
            model_name='playerfeedback',
            index=models.Index(fields=['game', '-created_at'], name='player_feed_game_id_85331e_idx'),
        ),
    ]
