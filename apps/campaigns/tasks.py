from celery import shared_task

from . import engine


@shared_task
def process_sequences():
    return engine.process_sequences()


@shared_task
def process_broadcasts():
    return engine.process_broadcasts()
