from .config import ListenerSettings
from .contract import ListenerEvent
from app.runtime.database import db_session



def handle_listener_event(event: ListenerEvent, settings: ListenerSettings) -> None:
    print(f"[event-listener] feature={settings.feature_key} listener={settings.listener_name} event={event.event_name} correlation_id={event.correlation_id}")
    with db_session() as db:
        _ = db


