import os
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify, send_from_directory
from .dictionaries import Dictionaries


def create_app(base_url: str = '') -> Flask:
    library_mode = os.getenv('SILVERDICT_LIBRARY') == '1'
    ui_root = Path(__file__).parent.parent / 'library_ui'
    app = Flask(__name__, static_folder=None if library_mode else '../build', static_url_path=f'{base_url}/')
    app.config['MAX_CONTENT_LENGTH'] = 64 * 1024
    Dictionaries(app)
    from .api import api
    app.register_blueprint(api, url_prefix=f'{base_url}/api')

    if library_mode:
        from .library import init_library
        from .library_fonts import init_library_fonts
        init_library(app)
        init_library_fonts(app)

        @app.errorhandler(Exception)
        def library_error(error):
            from werkzeug.exceptions import HTTPException
            if isinstance(error, HTTPException):
                if request.path.startswith('/api/'):
                    return jsonify(error=error.description), error.code
                return error
            app.logger.exception('Library request failed')
            return jsonify(error='The request could not complete. Please retry.'), 500

        @app.before_request
        def protect_library():
            # The library has a narrow API; legacy management has stateful GETs.
            if request.path.startswith('/api/') and not request.path.startswith(('/api/library/', '/api/cache/')):
                return jsonify(error='Use the library interface'), 404
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                expected = os.getenv('SILVERDICT_PUBLIC_URL', request.host_url).rstrip('/')
                origin = request.headers.get('Origin')
                if request.headers.get('X-SilverDict-Library') != '1' or (origin is not None and origin != expected):
                    return jsonify(error='A same-origin library request is required'), 403
            if request.path.startswith('/api/cache/'):
                from .resource_paths import ASSET_SUFFIXES
                if Path(request.path).suffix.lower() not in ASSET_SUFFIXES:
                    return jsonify(error='Not a display resource'), 404

        @app.after_request
        def security_headers(response):
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Referrer-Policy'] = 'no-referrer'
            if request.path == '/library-frame':
                origin = os.getenv('SILVERDICT_PUBLIC_URL', request.host_url).rstrip('/')
                cache = origin + '/api/cache/'
                fonts = origin + '/library-fonts/'
                response.headers['Content-Security-Policy'] = (
                    "sandbox allow-scripts; default-src 'none'; "
                    f"script-src 'unsafe-inline' 'unsafe-eval' {cache} {origin}/library-assets/frame.js; "
                    f"style-src 'unsafe-inline' {cache} {fonts}fonts.css; img-src data: {cache}; "
                    f"media-src data: {cache}; font-src data: {cache} {fonts}; connect-src {cache}; "
                    f"base-uri {cache}; form-action 'none'; frame-ancestors 'self'"
                )
            elif request.path.startswith(('/api/cache/', '/library-fonts/')):
                # Sandboxed opaque-origin frames need CORS for local fonts.
                response.headers['Access-Control-Allow-Origin'] = '*'
                response.headers['Content-Security-Policy'] = "sandbox allow-scripts; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"
            else:
                response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self' about:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
                response.headers['Cache-Control'] = 'no-store'
            return response

        @app.route('/library-frame')
        def library_frame():
            return send_from_directory(ui_root, 'frame.html')

        @app.route('/library-assets/<path:filename>')
        def library_assets(filename):
            return send_from_directory(ui_root, filename)

        @app.route('/')
        @app.route('/library')
        def index():
            return send_from_directory(ui_root, 'index.html')
    else:
        index_string = (Path(__file__).parent.parent / 'build/index.html').read_text()
        @app.route(f'{base_url}/')
        def index():
            return render_template_string(index_string, base_url=base_url)

    return app
