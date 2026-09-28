# @ai-generated file tool=claude-code model=claude-opus-5-5 reviewed-by=@r2vichan
import pytest

import app.db
from app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app.db, "DATABASE_PATH", str(tmp_path / "test.db"))
    return create_app().test_client()


def test_create_and_get_task(client):
    res = client.post("/tasks", json={"title": "  Buy milk  "})
    assert res.status_code == 201
    task = res.get_json()
    assert task["title"] == "Buy milk"
    assert task["done"] is False

    res = client.get(f"/tasks/{task['id']}")
    assert res.status_code == 200
    assert res.get_json() == task


def test_create_requires_title(client):
    assert client.post("/tasks", json={}).status_code == 400
    assert client.post("/tasks", json={"title": "   "}).status_code == 400
    assert client.post("/tasks", data="not json").status_code == 400


def test_list_and_filter(client):
    a = client.post("/tasks", json={"title": "a"}).get_json()
    client.post("/tasks", json={"title": "b"})
    client.patch(f"/tasks/{a['id']}", json={"done": True})

    assert [t["title"] for t in client.get("/tasks").get_json()["tasks"]] == ["a", "b"]
    assert [t["title"] for t in client.get("/tasks?done=true").get_json()["tasks"]] == ["a"]
    assert [t["title"] for t in client.get("/tasks?done=false").get_json()["tasks"]] == ["b"]
    assert client.get("/tasks?done=maybe").status_code == 400


def test_update_task(client):
    task = client.post("/tasks", json={"title": "old"}).get_json()
    res = client.patch(f"/tasks/{task['id']}", json={"title": "new", "done": True})
    assert res.status_code == 200
    assert res.get_json()["title"] == "new"
    assert res.get_json()["done"] is True

    assert client.patch(f"/tasks/{task['id']}", json={}).status_code == 400
    assert client.patch(f"/tasks/{task['id']}", json={"done": "yes"}).status_code == 400
    assert client.patch("/tasks/999", json={"done": True}).status_code == 404


def test_delete_task(client):
    task = client.post("/tasks", json={"title": "gone"}).get_json()
    assert client.delete(f"/tasks/{task['id']}").status_code == 204
    assert client.get(f"/tasks/{task['id']}").status_code == 404
    assert client.delete(f"/tasks/{task['id']}").status_code == 404
