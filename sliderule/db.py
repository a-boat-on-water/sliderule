"""Database access. psycopg v3, no ORM. DATABASE_URL comes from the environment."""

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()


def connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(url)
