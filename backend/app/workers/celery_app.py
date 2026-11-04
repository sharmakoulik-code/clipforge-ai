from celery import Celery

from app.core.config import settings

celery = Celery("video_repurposer", broker=settings.redis_url, backend=settings.redis_url)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_track_started=True,
    task_publish_retry=False,
)
celery.autodiscover_tasks(["app.workers"])


@celery.task(name="app.workers.celery_app.worker_health")
def worker_health() -> str:
    return "ok"
