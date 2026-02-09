from django.contrib import admin
from django.urls import path, include
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

urlpatterns = [
    path('', ensure_csrf_cookie(TemplateView.as_view(template_name='game/index.html')), name='home'),
    path('admin/', admin.site.urls),
    path('api/', include('game.urls')),
    path('api/auth/', include('rest_framework.urls')),
]
