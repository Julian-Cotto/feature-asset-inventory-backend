from pydantic import BaseModel

class StandardResponse(BaseModel):
    success: bool = True
    message: str | None = None