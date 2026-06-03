from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('splitter', '0002_splitjob_compress_level_splitjob_should_split_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='splitjob',
            name='processing_warnings',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Avisos sobre limitações encontradas no processamento'
            ),
        ),
    ]
