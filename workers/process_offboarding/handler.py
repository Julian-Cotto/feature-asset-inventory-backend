from .config import WorkerSettings
from .contract import WorkerEvent
from app.runtime.database import db_session



def handle_event(event: WorkerEvent, settings: WorkerSettings) -> None:
    print(f"[event-worker] feature={settings.feature_key} worker={settings.worker_name} event={event.event_name} correlation_id={event.correlation_id}")
    with db_session() as db:
        _ = db


