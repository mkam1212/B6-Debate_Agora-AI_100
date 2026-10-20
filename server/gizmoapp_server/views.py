from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from flask import Flask, abort, current_app, render_template, request, send_from_directory

from .config import scoped_path
from .db import database_summary, fetch_sample_nodes, get_db


def _public_prefix() -> str:
    # ProxyFix supplies SCRIPT_NAME only when TRUST_PROXY is enabled. Shared
    # app routes stay unprefixed; each member's trusted proxy supplies its own
    # browser mount. Never mutate application configuration between requests.
    return current_app.config["URL_PREFIX"] or request.script_root


def _root_path() -> str:
    return scoped_path(_public_prefix())


def _client_config() -> dict:
    prefix = _public_prefix()
    return {
        "apiBase": scoped_path(prefix, "api").rstrip("/"),
        "requestTimeoutMs": current_app.config["REQUEST_TIMEOUT_MS"],
    }


def _static_file_response(folder: str, asset_path: str):
    root = Path(current_app.config["STATIC_ROOT"]) / folder
    return send_from_directory(root, asset_path)


def register_page_routes(app: Flask) -> None:
    prefix = app.config["URL_PREFIX"]
    root_path = scoped_path(prefix)
    reserved_top_level = {"api", "app", "icons", "admin", "healthz", "readyz", "manifest.webmanifest", "sw.js", "favicon.ico"}

    @app.get(scoped_path(prefix, "app/<path:asset_path>"))
    def app_assets(asset_path: str):
        return _static_file_response("app", asset_path)

    @app.get(scoped_path(prefix, "icons/<path:asset_path>"))
    def icon_assets(asset_path: str):
        return _static_file_response("icons", asset_path)

    @app.get(scoped_path(prefix, "favicon.ico"))
    def favicon():
        return _static_file_response("icons", "icon-192.png")

    if "admin" in app.config["ENABLED_FEATURES"]:
        @app.get(scoped_path(prefix, "admin/"))
        def admin_page():
            summary = database_summary(current_app.config)
            sample_nodes = fetch_sample_nodes(get_db())
            return render_template(
                "admin.html",
                app_name=current_app.config["APP_NAME"],
                shell_label=current_app.config["APP_SHELL_LABEL"],
                shell_variant=current_app.config["APP_SHELL"],
                root_path=_root_path(),
                now=datetime.now(UTC).isoformat(),
                summary=summary,
                sample_nodes=sample_nodes,
                notes=current_app.config["ADMIN_NOTES"],
                available_shells=current_app.config["AVAILABLE_SHELLS"],
            )

    @app.route(root_path, defaults={"path": ""})
    @app.route(f"{root_path}<path:path>")
    def index(path: str):
        if path and path.split("/", 1)[0] in reserved_top_level:
            abort(404)

        public_prefix = _public_prefix()
        return render_template(
            current_app.config["APP_SHELL_TEMPLATE"],
            app_name=current_app.config["APP_NAME"],
            app_tagline=current_app.config["APP_TAGLINE"],
            shell_description=current_app.config["APP_SHELL_DESCRIPTION"],
            base_href=_root_path(),
            shared_asset_base=scoped_path(public_prefix, "app/"),
            asset_base=scoped_path(public_prefix, current_app.config["APP_SHELL_ASSET_SUBPATH"]),
            icon_url=scoped_path(public_prefix, "icons/icon-192.png"),
            theme_color=current_app.config["THEME_COLOR"],
            client_config=_client_config(),
        )
