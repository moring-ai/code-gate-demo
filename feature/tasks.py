# @ai-generated file tool=claude-code model=claude-opus-5-5 reviewed-by=@r2vichan
"""Tasks API: a minimal to-do list backed by the app's SQLite database."""
import sqlite3

from flask import Blueprint, request

from app.db import get_db

bp = Blueprint("tasks", __name__, url_prefix="/tasks")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _db() -> sqlite3.Connection:
    db = get_db()
    db.execute(SCHEMA)
    return db


def _to_dict(row) -> dict:
    task_id, title, done, created_at = row
    return {"id": task_id, "title": title, "done": bool(done), "created_at": created_at}


def _fetch(task_id: int):
    return _db().execute(
        "SELECT id, title, done, created_at FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()


def _error(message: str, status: int):
    return {"error": message}, status


@bp.get("")
def list_tasks():
    query = "SELECT id, title, done, created_at FROM tasks"
    params: tuple = ()
    done = request.args.get("done")
    if done is not None:
        if done not in ("true", "false"):
            return _error("`done` must be 'true' or 'false'", 400)
        query += " WHERE done = ?"
        params = (1 if done == "true" else 0,)
    rows = _db().execute(query + " ORDER BY id", params).fetchall()
    return {"tasks": [_to_dict(r) for r in rows]}


@bp.post("")
def create_task():
    data = request.get_json(silent=True) or {}
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        return _error("`title` is required and must be a non-empty string", 400)
    db = _db()
    cur = db.execute("INSERT INTO tasks (title) VALUES (?)", (title.strip(),))
    db.commit()
    return _to_dict(_fetch(cur.lastrowid)), 201


@bp.get("/<int:task_id>")
def get_task(task_id: int):
    row = _fetch(task_id)
    if row is None:
        return _error("task not found", 404)
    return _to_dict(row)


@bp.patch("/<int:task_id>")
def update_task(task_id: int):
    if _fetch(task_id) is None:
        return _error("task not found", 404)
    data = request.get_json(silent=True) or {}
    updates: dict = {}
    if "title" in data:
        if not isinstance(data["title"], str) or not data["title"].strip():
            return _error("`title` must be a non-empty string", 400)
        updates["title"] = data["title"].strip()
    if "done" in data:
        if not isinstance(data["done"], bool):
            return _error("`done` must be a boolean", 400)
        updates["done"] = int(data["done"])
    if not updates:
        return _error("provide `title` and/or `done`", 400)
    db = _db()
    # Column names come from the fixed keys above, never from user input.
    assignments = ", ".join(f"{col} = ?" for col in updates)
    db.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", (*updates.values(), task_id))
    db.commit()
    return _to_dict(_fetch(task_id))


@bp.delete("/<int:task_id>")
def delete_task(task_id: int):
    db = _db()
    cur = db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    if cur.rowcount == 0:
        return _error("task not found", 404)
    return "", 204
