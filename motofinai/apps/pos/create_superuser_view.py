from django.contrib.auth.models import User
from django.http import HttpResponse

def create_superuser(request):
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser('admin', 'admin@example.com', 'yourpassword')
        return HttpResponse("Superuser created!")
    return HttpResponse("Already exists.")