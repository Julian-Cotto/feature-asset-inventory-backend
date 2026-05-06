from .config import WorkerSettings
from .contract import WorkerEvent
from .handler import handle_event

def test_handle_event_executes_without_error() -> None:
    settings = WorkerSettings(feature_key="asset-inventory", worker_name="process-offboarding", trigger_kind="topic-subscription", topic_name="hr-events", subscription_name="asset-inventory-offboarding", queue_name="", event_name="UserOffboarded", dead_letter_enabled=True, max_concurrency=2, max_retries=3, retry_backoff="exponential")
    handle_event(WorkerEvent(event_name="UserOffboarded", payload={"sample": True}, correlation_id="test"), settings)