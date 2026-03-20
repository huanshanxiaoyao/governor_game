from django.apps import AppConfig


class GameConfig(AppConfig):
    name = 'game'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        import game.signals  # noqa: F401  注册登录信号
