from pydantic import BaseModel

class HealthResponse(BaseModel):
    status: str
    service: str
    feature_key: str
    auth_mode: str