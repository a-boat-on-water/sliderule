"""Shared FastAPI dependencies."""

from collections.abc import Iterator

import psycopg

from sliderule import db


def get_conn() -> Iterator[psycopg.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()
