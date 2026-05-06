from pydantic import BaseModel

class DomainEvent(BaseModel):
    name: str
    payload: dict