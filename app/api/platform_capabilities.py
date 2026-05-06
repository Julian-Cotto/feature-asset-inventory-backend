from fastapi import APIRouter

from fastapi import Depends
from sqlalchemy.orm import Session
from app.platform.database.dependencies import get_db


router = APIRouter(tags=["platform"])


@router.get("/platform/capabilities")
def get_platform_capabilities(
    db: Session = Depends(get_db),
):
    response = {
        "databaseEnabled": {{ "true" if database_enabled else "false" }},
        "blobStorageEnabled": {{ "true" if blob_storage_enabled else "false" }},
    }

    response["databaseDependency"] = db is not None


    return response