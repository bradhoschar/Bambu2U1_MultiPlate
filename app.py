#!/usr/bin/env python3
"""
Bambu2U1 Web Application
Web UI and REST API for converting Bambu Studio / MakerWorld 3MF projects
to Snapmaker U1 format.
"""

import os
import sys
import zipfile
import tempfile
import webbrowser
from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename

from converter import parse_bambu_project, convert_plate_to_u1, convert_all_plates

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(APP_DIR, "templates")
UPLOAD_FOLDER = os.path.join(APP_DIR, "uploads")
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

    save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    f.save(save_path)

    try:
        info = parse_bambu_project(save_path)
        info['server_filename'] = filename
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': f'Failed to parse 3MF file: {str(e)}'}), 500

@app.route('/api/convert_all')
def convert_all():
    filename = request.args.get('filename', '')
    safe_name = secure_filename(filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Source file not found'}), 404

    base_name = os.path.splitext(safe_name)[0]
    out_dir = os.path.join(app.config['UPLOAD_FOLDER'], f"{base_name}_converted")
    
    try:
        converted_files = convert_all_plates(filepath, out_dir, TEMPLATES_DIR)
        
        # Package into a zip for single-click download
        zip_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{base_name}_Snapmaker_U1_Plates.zip")
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
    filename = request.args.get('filename', '')
    safe_name = secure_filename(filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Source file not found'}), 404

    try:
        pid = int(request.args.get('plate', 1))
    except ValueError:
        return jsonify({'error': 'Invalid plate ID'}), 400

    base_name = os.path.splitext(safe_name)[0]
    out_file = os.path.join(app.config['UPLOAD_FOLDER'], f"{base_name}_U1_Plate_{pid:02d}.3mf")

    try:
        convert_plate_to_u1(filepath, pid, out_file, TEMPLATES_DIR)
        return send_file(
            out_file,
            as_attachment=True,
            download_name=os.path.basename(out_file)
        )
    except Exception as e:
        return jsonify({'error': f'Plate conversion failed: {str(e)}'}), 500

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Start the Bambu2U1 Web Server")
    parser.add_argument("--port", type=int, default=5000, help="Web server port (default: 5000)")
    parser.add_argument("--host", default="0.0.0.0", help="Web server host (default: 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser")
    args = parser.parse_args()

    url = f"http://localhost:{args.port}"
    print(f"\n=======================================================")
    print(f"  Bambu to Snapmaker U1 Multi-Plate Converter")
    print(f"  Web UI running at: {url}")
    print(f"=======================================================\n")

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    app.run(host=args.host, port=args.port, debug=False)

if __name__ == '__main__':
    main()
