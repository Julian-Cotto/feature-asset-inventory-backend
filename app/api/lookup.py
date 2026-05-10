from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.dependencies import require_permissions
from app.platform.auth_context import RequestAuthContext
from app.services.lookup_service import lookup_device


router = APIRouter(tags=["lookup"])


def require_view():
    return require_permissions(*get_settings().auth_required_permissions)


@router.get("/lookup")
def lookup(
    code: str = Query(..., min_length=1, max_length=128),
    db: Session = Depends(get_db),
    _: RequestAuthContext = Depends(require_view()),
):
    return lookup_device(db, code).to_dict()
