from celery import shared_task

from .importer import run_import


@shared_task(acks_late=True)
def import_contacts(job_id):
    job = run_import(job_id)
    return {"created": job.created_count, "updated": job.updated_count, "skipped": job.skipped_count, "errors": job.error_count}
