"""Flask application factory."""
import atexit
import os
from pathlib import Path
from werkzeug.security import check_password_hash

from flask import Flask, jsonify, request

from .controllers.web import web
from .controllers.library import library
from .controllers.events import events
from .models.sources import SourceManager
from .licensing import LicenceClient
from .metadata import collect_snapshot
from .controllers.licensing import licensing


def create_app(data_dir=None, model_dir=None):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024
    # The dashboard is a server-rendered template with separately served JavaScript.
    # Reload changed templates so a running development server cannot mix old HTML
    # with new client code.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    if os.getenv("VDM_REQUIRE_LICENCE") == "1" and not os.getenv("VDM_CLOUD_URL"):
        raise RuntimeError("VDM_CLOUD_URL is required for this managed installation.")
    data_root = Path(data_dir or os.getenv("VMD_DATA_DIR", "data")).resolve()
    licence = LicenceClient(os.getenv("VDM_CLOUD_URL", ""), data_root)
    app.extensions["vdm_licence"] = licence
    selected_models = model_dir or os.getenv("VMD_MODEL_DIR", "models")
    initial_data = data_root / 'awaiting-activation' if licence.managed else data_root
    app.extensions["vmd_manager"] = SourceManager(initial_data, selected_models, licence=licence)
    atexit.register(lambda: app.extensions["vmd_manager"].close())
    def change_workspace(context):
        org, group = context.get('organisation_id'), context.get('group_id')
        destination = data_root / 'workspaces' / f'org-{org}' / (f'group-{group}' if group else 'organisation') if org else data_root / 'awaiting-activation'
        current = app.extensions['vmd_manager']
        if current.data_dir.resolve() == destination.resolve():
            return
        current.close()
        app.extensions['vmd_manager'] = SourceManager(destination, selected_models, licence=licence)
    licence.scope_changed = change_workspace
    access_file = data_root / "access.hash"
    app.config["VMD_ACCESS_HASH"] = access_file.read_text().strip() if access_file.exists() else None
    licence.metadata_provider = lambda: collect_snapshot(app.extensions["vmd_manager"])
    licence.start()
    atexit.register(licence.close)
    app.register_blueprint(licensing)
    app.register_blueprint(web)
    app.register_blueprint(library)
    app.register_blueprint(events)

    @app.errorhandler(413)
    def upload_too_large(_error):
        return jsonify(error="Video is too large (2 GB limit)."), 413

    @app.before_request
    def local_request():
        if request.host.split(":")[0] not in {"127.0.0.1", "localhost", "[::1]"}:
            return jsonify(error="Local access only."), 403
        password_hash = app.config["VMD_ACCESS_HASH"]
        if password_hash:
            auth = request.authorization
            if not auth or auth.username != "operator" or not check_password_hash(password_hash, auth.password or ""):
                response = jsonify(error="Operator sign-in required.")
                response.status_code = 401
                response.headers["WWW-Authenticate"] = 'Basic realm="VMD device", charset="UTF-8"'
                return response
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            return jsonify(error="Cross-origin access is not allowed."), 403
        if request.method != "GET" and request.headers.get("X-VMD-Client") != "dashboard":
            return jsonify(error="Local client header required."), 403
        state = licence.status()
        if (state.get('group_id') and not state['valid'] and request.endpoint
                not in {'licensing.activation_page', 'licensing.licence_status', 'licensing.licence_action', 'static'}):
            if request.path.startswith('/api/'):
                return jsonify(error='Group access is locked. Reconnect or contact your administrator.'), 402
            from flask import redirect
            return redirect('/activation')
        if request.endpoint in {"web.add_source", "web.train_model"} and not licence.status()["valid"]:
            return jsonify(error="Processing is locked. Open Activation & licence to check access."), 402

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else "no-cache"
        return response

    return app
