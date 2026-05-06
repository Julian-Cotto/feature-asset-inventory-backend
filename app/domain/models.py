from dataclasses import dataclass

@dataclass(slots=True)
class FeatureDomainItem:
    id: str
    name: str
    status: str