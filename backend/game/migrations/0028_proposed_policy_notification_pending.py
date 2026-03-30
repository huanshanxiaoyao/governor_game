from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0027_judicial_async_review'),
    ]

    operations = [
        migrations.AddField(
            model_name='proposedpolicy',
            name='notification_pending',
            field=models.BooleanField(
                default=False,
                help_text='审批完成但通知尚未写入county_data',
            ),
        ),
    ]
