"""Entrypoint for the dedicated research-task outbox dispatcher."""
import logging

from app.services.task_outbox import run_dispatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

if __name__ == "__main__":
    run_dispatcher()
