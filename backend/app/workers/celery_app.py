from celery import Celery
from app.config import settings

celery_app = Celery(
    "nexora",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.docking_tasks",
        "app.workers.analysis_tasks",
        "app.workers.hsa_tasks",
        "app.workers.research_structure_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "app.workers.docking_tasks.*": {"queue": "docking"},
        "app.workers.analysis_tasks.*": {"queue": "analysis"},
        "app.workers.hsa_tasks.*": {"queue": "qsa"},
        "hsa.run": {"queue": "qsa"},
        "qsa.run": {"queue": "qsa"},
        "app.workers.research_structure_tasks.*": {"queue": "structure"},
    },
    task_time_limit=3600,
    task_soft_time_limit=3000,
    worker_prefetch_multiplier=1,
    result_expires=86400,
)
