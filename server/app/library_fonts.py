"""Optional shared local web fonts for opaque-origin dictionary frames."""
import os
from pathlib import Path
from flask import Response, abort, send_from_directory

FONT_MIMETYPES = {
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf': 'font/ttf',
    '.otf': 'font/otf',
}


def init_library_fonts(app):
    if os.getenv('SILVERDICT_LIBRARY') != '1':
        return
    root = Path(os.getenv('SILVERDICT_FONTS') or '/library/fonts').expanduser().absolute()

    @app.get('/library-fonts/<path:filename>')
    def library_font(filename):
        # Only a single root-level filename is accepted. This directory is not a
        # general static-file endpoint, and its CSS cannot expose sibling state.
        if '/' in filename or '\\' in filename or filename in ('.', '..') or any(ord(char) < 32 for char in filename):
            abort(404)
        suffix = Path(filename).suffix.lower()
        if filename != 'fonts.css' and suffix not in FONT_MIMETYPES:
            abort(404)
        path = root / filename
        if root.is_symlink() or path.is_symlink():
            abort(404)
        if filename == 'fonts.css' and not path.exists():
            response = Response('', mimetype='text/css')
            response.headers['Cache-Control'] = 'no-store'
        else:
            if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
                abort(404)
            mimetype = 'text/css' if filename == 'fonts.css' else FONT_MIMETYPES[suffix]
            response = send_from_directory(root, filename, mimetype=mimetype, conditional=True,
                                           max_age=0 if filename == 'fonts.css' else 3600)
        # Font requests originate from sandboxed documents with Origin: null.
        # No credentials or management API CORS permission is granted.
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
