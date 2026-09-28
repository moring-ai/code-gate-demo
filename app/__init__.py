from flask import Flask

from app.db import close_db


def create_app() -> Flask:
    app = Flask(__name__)
    app.teardown_appcontext(close_db)

    @app.get("/health")
    def health():
        return {"ok": True}

    # @ai-generated begin tool=claude-code model=claude-opus-5-5 reviewed-by=@r2vichan
    from feature import tasks_bp

    app.register_blueprint(tasks_bp)
    # @ai-generated end

    return app
