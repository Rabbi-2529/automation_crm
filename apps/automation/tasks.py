from celery import shared_task

from . import engine


@shared_task(acks_late=True)
def process_activity(activity_id):
    return len(engine.process_activity_id(activity_id))


@shared_task
def resume_waiting_runs():
    return engine.resume_due_runs()


@shared_task
def scan_scheduled_triggers():
    return engine.scan_scheduled()
