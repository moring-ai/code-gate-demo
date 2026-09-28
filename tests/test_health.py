from app import create_app


def test_health():
    client = create_app().test_client()
    assert client.get("/health").get_json() == {"ok": True}
