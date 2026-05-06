from collections.abc import Generator

from .session import get_db as _get_db


def get_db() -> Generator:
    yield from _get_db()