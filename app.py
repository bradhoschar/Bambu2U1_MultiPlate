#!/usr/bin/env python3
"""
Bambu2U1 Web Application - v2.0.0
Web UI and REST API for converting Bambu Studio / MakerWorld 3MF projects
to Snapmaker U1 format with session-isolated uploads.
"""

import os
import sys
import uuid
import zipfile
import tempfile
import webbrowser
import urllib.request
import time
import threading
from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename

from converter import parse_bambu_project, convert_plate_to_u1, convert_all_plates, get_default_templates_dir

TEMPLATES_DIR = get_default_templates_dir()
if getattr(sys, 'frozen', False):
    UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), 'bambu2u1_uploads')
else:
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, template_folder=TEMPLATES_DIR)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/analyze', methods=['POST'])
def analyze():
    f = request.files.get('file')
    if not f or f.filename == '':
        return jsonify({'error': 'No file uploaded'}), 400

    filename = secure_filename(f.filename)
    if not filename.lower().endswith('.3mf'):
        return jsonify({'error': 'File must be a .3mf project file'}), 400

    session_id = str(uuid.uuid4())
    session_dir = os.path.join(app.config['UPLOAD_FOLDER'], session_id)
    os.makedirs(session_dir, exist_ok=True)

    save_path = os.path.join(session_dir, filename)
    f.save(save_path)

    try:
        info = parse_bambu_project(save_path)
        info['server_filename'] = filename
        info['session_id'] = session_id
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': f'Failed to parse 3MF file: {str(e)}'}), 500


@app.route('/api/convert_all')
def convert_all():
    session_id = request.args.get('session_id', '')
    filename = request.args.get('filename', '')

    safe_name = secure_filename(filename)
    if session_id:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(session_id), safe_name)
        session_dir = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(session_id))
    else:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
        session_dir = app.config['UPLOAD_FOLDER']

    if not os.path.exists(filepath):
        return jsonify({'error': 'Source file not found'}), 404

    base_name = os.path.splitext(safe_name)[0]
    out_dir = os.path.join(session_dir, f"{base_name}_converted")

    try:
        converted_files = convert_all_plates(filepath, out_dir, TEMPLATES_DIR)

        zip_path = os.path.join(session_dir, f"{base_name}_Snapmaker_U1_Plates.zip")
        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for fpath in converted_files:
                zout.write(fpath, arcname=os.path.basename(fpath))

        return send_file(
            zip_path,
            as_attachment=True,
            download_name=f"{base_name}_Snapmaker_U1_All_Plates.zip"
        )
    except Exception as e:
        return jsonify({'error': f'Conversion failed: {str(e)}'}), 500


@app.route('/api/convert_plate')
def convert_single():
    session_id = request.args.get('session_id', '')
    filename = request.args.get('filename', '')

    safe_name = secure_filename(filename)
    if session_id:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(session_id), safe_name)
        session_dir = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(session_id))
    else:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
        session_dir = app.config['UPLOAD_FOLDER']

    if not os.path.exists(filepath):
        return jsonify({'error': 'Source file not found'}), 404

    try:
        pid = int(request.args.get('plate', 1))
    except ValueError:
        return jsonify({'error': 'Invalid plate ID'}), 400

    base_name = os.path.splitext(safe_name)[0]
    out_file = os.path.join(session_dir, f"{base_name}_U1_Plate_{pid:02d}.3mf")

    try:
        convert_plate_to_u1(filepath, pid, out_file, TEMPLATES_DIR)
        return send_file(
            out_file,
            as_attachment=True,
            download_name=os.path.basename(out_file)
        )
    except Exception as e:
        return jsonify({'error': f'Plate conversion failed: {str(e)}'}), 500


def open_browser_when_ready(url):
    """Poll localhost until web server responds, then open browser."""
    for _ in range(40):
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.25)
    webbrowser.open(url)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Start the Bambu2U1 Web Server - v2.0")
    parser.add_argument("--port", type=int, default=5000, help="Web server port (default: 5000)")
    parser.add_argument("--host", default="0.0.0.0", help="Web server host (default: 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser")
    args = parser.parse_args()

    url = f"http://localhost:{args.port}"
    print(f"\n=======================================================")
    print(f"  Bambu to Snapmaker U1 Multi-Plate Converter v2.0.0")
    print(f"  Web UI running at: {url}")
    print(f"=======================================================\n")

    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
