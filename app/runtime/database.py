from contextlib import contextmanager

from app.platform.database.session import SessionLocal


@contextmanager
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()