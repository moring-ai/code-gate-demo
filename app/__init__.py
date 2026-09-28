from flask import Flask

from app.db import close_db


def create_app() -> Flask:
    app = Flask(__name__)
    app.teardown_appcontext(close_db)

    @app.get("/health")
    def health():
        return {"ok": True}

    return app
