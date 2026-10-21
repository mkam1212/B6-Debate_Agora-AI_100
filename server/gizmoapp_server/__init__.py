from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask


def create_app(test_config: dict | None = None, shell_variant: str | None = None) -> Flask:
    from flask import Flask
    from flask.json.provider import DefaultJSONProvider
    from werkzeug.middleware.proxy_fix import ProxyFix

    from .api import register_api_routes
    from .config import load_settings
    from .db import close_db, initialize_database, verify_database_schema
    from .debate_api import register_debate_routes
    from .account_api import register_account_routes
    from .attachments import register_attachment_routes
    from .shells import shell_settings
    from .views import register_page_routes


    class StrictJSONProvider(DefaultJSONProvider):
        def dumps(self, obj, **kwargs):
            kwargs.setdefault("allow_nan", False)
            return super().dumps(obj, **kwargs)


    app = Flask(__name__, template_folder="templates", static_folder=None)
    app.json = StrictJSONProvider(app)
    app.config.update(load_settings(shell_variant=shell_variant))

    if test_config:
        app.config.update(test_config)
        # Tests explicitly opt into the real-tool adapter and replace its network transport.
        if app.config["TESTING"] and "WEB_SEARCH_ENABLED" not in test_config:
            app.config["WEB_SEARCH_ENABLED"] = False
    if not test_config or "SESSION_COOKIE_PATH" not in test_config:
        app.config["SESSION_COOKIE_PATH"] = app.config["URL_PREFIX"] or "/"

    app.config.update(shell_settings(app.config.get("APP_SHELL")))

    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_port=1, x_proto=1, x_prefix=1)

    if app.config["AUTO_MIGRATE"]:
        initialize_database(app.config)
    else:
        verify_database_schema(app.config)
    app.teardown_appcontext(close_db)

    from .media_routes import register_media_routes
    register_media_routes(app)
    register_api_routes(app)
    register_debate_routes(app)
    register_account_routes(app)
    register_attachment_routes(app)
    from .session_access import register_session_access_routes
    register_session_access_routes(app)
    register_page_routes(app)
    return app
