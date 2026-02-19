from django.db import migrations

def create_superuser(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser('admin', 'admin@example.com', 'yourpassword')

class Migration(migrations.Migration):
    dependencies = [
        # Add your app's dependencies here
    ]

    operations = [
        migrations.RunPython(create_superuser),
    ]