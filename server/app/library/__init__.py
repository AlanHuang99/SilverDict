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

    @api.route('/reading-settings', methods=['GET', 'PUT'])
    def reading_settings():
        body = request.get_json(silent=True) if request.method == 'PUT' else None
        if request.method == 'PUT' and not isinstance(body, dict):
            return jsonify(error='Settings must be an object'), 400
        group = body.get('group', 'Default Group') if body is not None else request.args.get('group', 'Default Group')
        try:
            rows = library.save_reading_settings(group, body.get('dictionaries')) if body is not None else library.reading_settings(group)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(group=group, dictionaries=rows)

    @api.get('/entry')
    def entry():
        group = request.args.get('group', 'Default Group')
        query = request.args.get('q', '').strip()
        identity = request.args.get('id', '')
        if group not in library.groups() or not query or len(query) > 1000:
            return jsonify(error='Invalid group or query'), 400
        if identity not in library.reading_dictionaries(group):
            return jsonify(error='Dictionary is not enabled in this group'), 404
        warnings = []
        articles = library.dictionaries.query(group, query, errors=warnings, dictionary_names=[identity])
        if warnings:
            return jsonify(error=warnings[0]['error']), 422
        if not articles:
            return jsonify(error='No entry found in this dictionary'), 404
        from ..resource_html import rewrite_article
        try:
            identity, title, html = articles[0]
            return jsonify(id=identity, title=title, html=rewrite_article(html, identity))
        except Exception:
            app.logger.exception('Could not render dictionary %s', identity)
            return jsonify(error='This entry could not be displayed.'), 422

    @api.get('/search')
    def search():
        group = request.args.get('group', 'Default Group')
        query = request.args.get('q', '').strip()
        if group not in library.groups():
            return jsonify(error='Unknown group'), 400
        if len(query) > 1000:
            return jsonify(error='Query is too long'), 400
        names = library.reading_dictionaries(group)
        if not query or not names:
            return jsonify(articles=[], suggestions=[])
        from ..resource_html import rewrite_article
        warnings = []
        deferred = request.args.get('deferred') == '1'
        articles = [] if deferred else library.dictionaries.query(group, query, errors=warnings, dictionary_names=names)
        rendered = library.dictionaries.matching_dictionaries(group, query, dictionary_names=names) if deferred else []
        for identity, title, html in articles:
            try:
                rendered.append({'id': identity, 'title': title, 'html': rewrite_article(html, identity)})
            except Exception:
                app.logger.exception('Could not render dictionary %s', identity)
                warnings.append({'id': identity, 'title': title, 'error': 'This entry could not be displayed.'})
        try:
            suggestions = library.dictionaries.suggestions(group, query, dictionary_names=names)
        except Exception:
            app.logger.exception('Could not generate suggestions')
            suggestions = []
            warnings.append({'id': 'suggestions', 'title': 'Suggestions', 'error': 'Suggestions are temporarily unavailable.'})
        return jsonify(articles=rendered, suggestions=suggestions, warnings=warnings)

    app.register_blueprint(api)
    return library
