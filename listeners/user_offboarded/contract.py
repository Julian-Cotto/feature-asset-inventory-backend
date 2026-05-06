from pydantic import BaseModel

class ListenerEvent(BaseModel):
    event_name: str
    payload: dict
    correlation_id: str