from .config import get_listener_settings
from .contract import ListenerEvent
from .handler import handle_listener_event

def main() -> int:
    settings = get_listener_settings()
    handle_listener_event(ListenerEvent(event_name=settings.event_name, payload={"message": "sample listener payload"}, correlation_id="local-dev"), settings)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())