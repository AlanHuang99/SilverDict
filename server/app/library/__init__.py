"""Personal library extension, enabled explicitly by the application factory."""
import os
from pathlib import Path
from flask import Blueprint, jsonify, request, send_file
from .jobs import Library


def init_library(app):
    library = Library(app.extensions['dictionaries'], os.environ['SILVERDICT_SOURCE'], os.environ['SILVERDICT_STATE'], os.environ['SILVERDICT_LIBRARY_DATA'], start=not app.config.get('LIBRARY_DISABLE_WORKER', False))
    app.extensions['library'] = library
    api = Blueprint('library', __name__, url_prefix='/api/library')

    @api.get('/health')
    def health():
        if not library.healthy():
            return jsonify(status='unhealthy', error='Library coordinator is not running'), 503
        return jsonify(status='ok')

    @api.post('/groups')
    def create_group():
        body = request.get_json(silent=True)
        try:
            groups = library.create_group(body.get('name') if isinstance(body, dict) else None)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(groups=groups)

    @api.get('/catalog')
    def catalog():
        return jsonify(items=library.items(), groups=library.groups())

    @api.route('/jobs', methods=['GET', 'POST'])
    def jobs():
        if request.method == 'GET':
            return jsonify(jobs=[{key: job[key] for key in ('id', 'source_id', 'action', 'status', 'progress', 'message', 'download_url', 'created_at')} for job in library.store.all()])
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get('source_id'), str):
            return jsonify(error='A source_id is required'), 400
        try:
            job = library.enqueue(body['source_id'], body.get('action'), body.get('group', 'Default Group'))
        except (ValueError, FileNotFoundError) as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(id=job['id'], status=job['status']), 202

    @api.get('/jobs/<identity>/download')
    def download(identity):
        job = library.store.get(identity)
        if not job or job['status'] != 'completed' or job['action'] != 'export' or not job['artifact']:
            return jsonify(error='Completed export not found'), 404
        path = Path(job['artifact']).resolve()
        if not path.is_relative_to((library.data / 'exports').resolve()) or not path.is_file():
            return jsonify(error='Export file not found'), 404
        return send_file(path, as_attachment=True, download_name=f"dictionary-{job['source_id']}.zip", mimetype='application/zip', conditional=True)

    @api.get('/search')
    def search():
        group = request.args.get('group', 'Default Group')
        query = request.args.get('q', '').strip()
        if group not in library.groups():
            return jsonify(error='Unknown group'), 400
        if len(query) > 1000:
            return jsonify(error='Query is too long'), 400
        if not query or not library.dictionaries.settings.dictionaries_of_group(group):
            return jsonify(articles=[], suggestions=[])
        from ..resource_html import rewrite_article
        articles = library.dictionaries.query(group, query)
        return jsonify(articles=[{'id': identity, 'title': title, 'html': rewrite_article(html, identity)} for identity, title, html in articles], suggestions=library.dictionaries.suggestions(group, query))

    app.register_blueprint(api)
    return library
