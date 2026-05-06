from pydantic import BaseModel


class FeatureItem(BaseModel):
    id: str
    name: str
    status: str


class FeatureItemsResponse(BaseModel):
    feature_key: str
    authenticated: bool
    user: str | None = None
    permissions: list[str] = []
    items: list[FeatureItem]