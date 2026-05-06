from pydantic import BaseModel

class WorkerEvent(BaseModel):
    event_name: str
    payload: dict
    correlation_id: str