"""
ASGI config for quiz_project project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quiz_project.settings')

# Должен быть вызван до импорта чего-либо, что импортирует модели
# (в т.ч. multiplayer.routing -> multiplayer.consumers -> multiplayer.models) —
# иначе Django ещё не готов (AppRegistryNotReady).
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

import multiplayer.routing

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AuthMiddlewareStack(
        URLRouter(multiplayer.routing.websocket_urlpatterns)
    ),
})
