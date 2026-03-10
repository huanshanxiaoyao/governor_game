from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0013_officialdom_expanded'),
    ]

    operations = [
        migrations.AddField(
            model_name='playerprofile',
            name='personal_wealth',
            field=models.FloatField(
                default=0.0,
                help_text='家产（两）：任内积累的个人财富，含合法薪俸与灰色所得',
            ),
        ),
        migrations.AddField(
            model_name='neighborcounty',
            name='governor_archetype',
            field=models.CharField(
                choices=[
                    ('VIRTUOUS', '循吏型'),
                    ('MIDDLING', '中庸守成型'),
                    ('CORRUPT', '贪酷恶劣型'),
                ],
                default='MIDDLING',
                help_text='知县施政类型（循吏/中庸/贪酷）',
                max_length=10,
            ),
        ),
    ]
