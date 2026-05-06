from .config import ListenerSettings
from .contract import ListenerEvent
from .handler import handle_listener_event

def test_handle_listener_event_executes_without_error() -> None:
    settings = ListenerSettings(feature_key="asset-inventory", listener_name="user-offboarded", event_name="hr.user.offboarded", payload_schema="UserOffboarded")
    handle_listener_event(ListenerEvent(event_name="hr.user.offboarded", payload={"sample": True}, correlation_id="test"), settings)