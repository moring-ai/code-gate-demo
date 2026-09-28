import os
import sqlite3

from flask import g

DATABASE_PATH = os.environ.get("DATABASE_PATH", "app.db")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()
