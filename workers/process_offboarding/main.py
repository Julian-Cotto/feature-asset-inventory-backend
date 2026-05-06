from .config import get_worker_settings
from .contract import WorkerEvent
from .handler import handle_event

def main() -> int:
    settings = get_worker_settings()
    handle_event(WorkerEvent(event_name=settings.event_name, payload={"message": "sample event payload"}, correlation_id="local-dev"), settings)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())