"""Which workflow run (if any) is executing right now.

activities.services.record() stamps this onto every Activity it writes, so the engine can tell events
caused by automations apart (for loop protection and the "Automation · …" label on the timeline).
Kept free of model imports so the activities app can use it without import cycles.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar("automation_run", default=None)


@contextmanager
def running(run):
    token = _current.set({"run": run.pk, "workflow": run.workflow_id, "name": run.workflow.name, "depth": run.depth})
    try:
        yield
    finally:
        _current.reset(token)


def marker():
    return _current.get()
