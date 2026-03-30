from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0026_tiered_policy_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='judicialcaseinstance',
            name='prefect_review_queued_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='judicialcaseinstance',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING_ASSISTANT_REVIEW', '待县丞意见'),
                    ('PENDING_MAGISTRATE_ROUND_1', '待知县一审'),
                    ('RETURNED_FOR_REVIEW', '打回重审'),
                    ('PENDING_MAGISTRATE_ROUND_2', '待知县二审'),
                    ('SUBMITTED_TO_PREFECT', '已上呈知府'),
                    ('DEFERRED_TO_PREFECT', '委托知府裁定'),
                    ('PREFECT_REVIEWING', '知府复审中'),
                    ('PREFECT_REVIEWED', '知府已决待投递'),
                    ('PREFECT_DECIDED', '知府已裁定'),
                    ('WITHDRAWN_THIS_QUARTER', '本季度暂缓'),
                ],
                default='PENDING_MAGISTRATE_ROUND_1',
                max_length=30,
            ),
        ),
    ]
