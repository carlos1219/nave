"""Arranque de `celery --app nave.bootstrap.celery_worker worker` (Celery busca `app`)."""

from nave.bootstrap.worker import worker_app

app = worker_app()
