"""Modulo de arranque para `celery --app nave.bootstrap.celery_beat beat` (Celery busca `app`)."""

from nave.bootstrap.scheduler import scheduler_app

app = scheduler_app()
