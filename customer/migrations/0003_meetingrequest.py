# Generated by Django 5.1.4 on 2025-01-11 20:27

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customer', '0002_alter_project_status'),
        ('dev', '0002_profile_manual_availability_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='MeetingRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('completed', 'Completed')], default='pending', max_length=20)),
                ('message', models.TextField(help_text='Message for the meeting')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('scheduled_time', models.DateTimeField(blank=True, null=True)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='customer.customerprofile')),
                ('developer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='dev.profile')),
            ],
        ),
    ]
