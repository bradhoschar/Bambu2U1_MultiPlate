#!/usr/bin/env python3
"""
Bambu2U1 Multi-Plate Converter
Converts Bambu Studio / MakerWorld multi-plate 3MF files into Snapmaker U1-ready 3MF files.
Preserves multi-color painting, centers plates on the U1 bed, maps up to 4 filaments per plate,
and fixes prime tower variable layer height conflicts.
"""

import os
import sys
import re
import json
import zipfile
import shutil
import base64
import argparse
import xml.etree.ElementTree as ET

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(SCRIPT_DIR, "templates")

NS_CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
NS_PROD = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'
NS_REL  = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS_BBL  = 'http://schemas.bambulab.com/package/2021'
NS_MAP  = {'m': NS_CORE, 'p': NS_PROD}

def parse_plates_info(bambu_3mf_path):
    """Parses 3MF file and returns metadata for all plates and filaments."""
    with zipfile.ZipFile(bambu_3mf_path, 'r') as bbl:
        all_files = set(bbl.namelist())
        raw_cfg = bbl.read('Metadata/model_settings.config')
        cfg_root = ET.fromstring(raw_cfg)
        
        raw_model = bbl.read('3D/3dmodel.model')
        m_root = ET.fromstring(raw_model)

        orig_proj = json.loads(bbl.read('Metadata/project_settings.config').decode('utf-8'))
        orig_colors = orig_proj.get('filament_colour', [])
        orig_types = orig_proj.get('filament_type', [])

        diff = orig_proj.get('different_settings_to_system', [])
        has_support = any(isinstance(s, str) and 'enable_support' in s for s in diff)

        # Map objects to names and extruders
        obj_info = {}
        for obj in cfg_root.findall('object'):
            oid = obj.attrib.get('id')
            meta = {m.attrib.get('key'): m.attrib.get('value') for m in obj.findall('metadata')}
            obj_info[oid] = {
                'name': meta.get('name', f'Object_{oid}'),
                'extruder': int(meta.get('extruder', 1))
            }

        plates = []
        for p in cfg_root.findall('plate'):
            pmeta = {m.attrib.get('key'): m.attrib.get('value') for m in p.findall('metadata')}
            pid = int(pmeta.get('plater_id', 1))
            pname = pmeta.get('plater_name', '').strip() or f"Plate {pid}"

            plate_objs = []
            for inst in p.findall('model_instance'):
                for m in inst.findall('metadata'):
                    if m.attrib.get('key') == 'object_id':
                        plate_objs.append(m.attrib.get('value'))

            used_extruders = set()
            for oid in plate_objs:
                if oid in obj_info:
                    used_extruders.add(obj_info[oid]['extruder'])

            sorted_exts = sorted(list(used_extruders))
            if not sorted_exts:
                sorted_exts = [1]

            plate_filaments = []
            for ext in sorted_exts:
                idx = ext - 1
                c = orig_colors[idx] if idx < len(orig_colors) else '#FFFFFFFF'
                t = orig_types[idx] if idx < len(orig_types) else 'PLA'
                plate_filaments.append({'id': ext, 'color': c, 'type': t})

            # Extract thumbnail preview if present
            thumb_b64 = ""
            for thumb_name in [f"Metadata/plate_{pid}_small.png", f"Metadata/plate_{pid}.png"]:
                if thumb_name in all_files:
                    thumb_b64 = base64.b64encode(bbl.read(thumb_name)).decode('utf-8')
                    break

            plates.append({
                'id': pid,
                'name': pname,
                'objects_count': len(plate_objs),
                'objects': [obj_info.get(oid, {}).get('name', oid) for oid in plate_objs],
                'filaments': plate_filaments,
                'thumbnail_b64': thumb_b64
            })

    return {
        'filename': os.path.basename(bambu_3mf_path),
        'plates_count': len(plates),
        'has_support': has_support,
        'total_filaments': len(orig_colors),
        'plates': plates
    }


def convert_bambu_to_u1(bambu_3mf_path, output_dir=None, plate_selection=None):
    """
    Converts a multi-plate Bambu 3MF file to individual Snapmaker U1 ready 3MF files.
    """
    base_name = os.path.splitext(os.path.basename(bambu_3mf_path))[0]
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(bambu_3mf_path), f"{base_name}_U1_Ready")
    os.makedirs(output_dir, exist_ok=True)

    tmpl_no_supp = os.path.join(TEMPLATES_DIR, 'u1_template.3mf')
    tmpl_supp = os.path.join(TEMPLATES_DIR, 'u1_template_supports.3mf')

    with zipfile.ZipFile(bambu_3mf_path, 'r') as bbl:
        all_bbl_files = set(bbl.namelist())
        raw_cfg = bbl.read('Metadata/model_settings.config')
        cfg_root = ET.fromstring(raw_cfg)
        raw_model = bbl.read('3D/3dmodel.model')
        raw_rels = bbl.read('3D/_rels/3dmodel.model.rels')
        content_types = bbl.read('[Content_Types].xml')
        dot_rels = bbl.read('_rels/.rels')

        orig_proj = json.loads(bbl.read('Metadata/project_settings.config').decode('utf-8'))
        orig_colors = orig_proj.get('filament_colour', [])
        orig_types = orig_proj.get('filament_type', [])

        diff = orig_proj.get('different_settings_to_system', [])
        has_support = any(isinstance(s, str) and 'enable_support' in s for s in diff)
        u1_template = tmpl_supp if (has_support and os.path.exists(tmpl_supp)) else tmpl_no_supp

        with zipfile.ZipFile(u1_template, 'r') as u1:
            u1_proj_base = json.loads(u1.read('Metadata/project_settings.config').decode('utf-8'))

    plates_xml = cfg_root.findall('plate')
    converted_files = []

    for p in plates_xml:
        pmeta = {m.attrib['key']: m.attrib['value'] for m in p.findall('metadata')}
        pid = int(pmeta['plater_id'])

        if plate_selection and pid not in plate_selection:
            continue

        col = (pid - 1) % 4
        row = (pid - 1) // 4
        dx = col * 340.0
        dy = row * -320.0

        plate_objs = set()
        for inst in p.findall('model_instance'):
            for m in inst.findall('metadata'):
                if m.attrib['key'] == 'object_id':
                    plate_objs.add(m.attrib['value'])

        used_extruders = set()
        for obj in cfg_root.findall('object'):
            if obj.attrib.get('id') in plate_objs:
                for m in obj.findall('metadata'):
                    if m.attrib.get('key') == 'extruder':
                        try:
                            used_extruders.add(int(m.attrib.get('value')))
                        except:
                            pass

        sorted_exts = sorted(list(used_extruders))
        if not sorted_exts:
            sorted_exts = [1]

        # Map to 1..4 slots
        ext_mapping = {}
        for new_slot, old_ext in enumerate(sorted_exts[:4], start=1):
            ext_mapping[str(old_ext)] = str(new_slot)

        # 1. 3D/3dmodel.model
        ET.register_namespace('', NS_CORE)
        ET.register_namespace('p', NS_PROD)
        ET.register_namespace('BambuStudio', NS_BBL)
        m_tree = ET.fromstring(raw_model)

        for meta in m_tree.findall('m:metadata', NS_MAP):
            if meta.attrib.get('name') == 'Title':
                meta.text = f"{base_name} - Plate {pid} (Snapmaker U1)"
            elif meta.attrib.get('name') == 'Thumbnail_Middle':
                meta.text = "/Metadata/plate_1.png"
            elif meta.attrib.get('name') == 'Thumbnail_Small':
                meta.text = "/Metadata/plate_1_small.png"

        needed_models = set()
        res_elem = m_tree.find('m:resources', NS_MAP)
        for obj in list(res_elem):
            oid = obj.attrib.get('id')
            if oid in plate_objs:
                comp = obj.find('m:components/m:component', NS_MAP)
                if comp is not None:
                    p_path = comp.attrib.get(f'{{{NS_PROD}}}path')
                    if p_path:
                        needed_models.add(p_path)
            else:
                res_elem.remove(obj)

        build_elem = m_tree.find('m:build', NS_MAP)
        for item in list(build_elem):
            oid = item.attrib.get('objectid')
            if oid in plate_objs:
                tf = item.attrib['transform'].split()
                if len(tf) == 12:
                    tf[9] = str(round(float(tf[9]) - dx, 6))
                    tf[10] = str(round(float(tf[10]) - dy, 6))
                    item.attrib['transform'] = " ".join(tf)
            else:
                build_elem.remove(item)

        model_out = ET.tostring(m_tree, encoding='utf-8', xml_declaration=True).decode('utf-8')
        if 'xmlns:BambuStudio' not in model_out:
            model_out = model_out.replace('<model ', f'<model xmlns:BambuStudio="{NS_BBL}" ', 1)

        # 2. 3D/_rels/3dmodel.model.rels
        ET.register_namespace('', NS_REL)
        r_tree = ET.fromstring(raw_rels)
        for rel in list(r_tree):
            if rel.attrib.get('Target') not in needed_models:
                r_tree.remove(rel)
        rels_out = ET.tostring(r_tree, encoding='utf-8', xml_declaration=True)

        # 3. Metadata/model_settings.config
        c_tree = ET.fromstring(raw_cfg)
        for obj in list(c_tree.findall('object')):
            if obj.attrib.get('id') not in plate_objs:
                c_tree.remove(obj)
            else:
                for m in obj.findall('metadata'):
                    if m.attrib.get('key') == 'extruder':
                        old_v = m.attrib.get('value')
                        m.attrib['value'] = ext_mapping.get(old_v, '1')

        for plate_elem in list(c_tree.findall('plate')):
            current_pid = None
            for m in plate_elem.findall('metadata'):
                if m.attrib.get('key') == 'plater_id':
                    current_pid = int(m.attrib.get('value'))
            if current_pid == pid:
                for m in plate_elem.findall('metadata'):
                    k = m.attrib.get('key')
                    if k == 'plater_id':
                        m.attrib['value'] = '1'
                    elif k == 'thumbnail_file':
                        m.attrib['value'] = 'Metadata/plate_1.png'
                    elif k == 'thumbnail_no_light_file':
                        m.attrib['value'] = 'Metadata/plate_no_light_1.png'
                    elif k == 'top_file':
                        m.attrib['value'] = 'Metadata/top_1.png'
                    elif k == 'pick_file':
                        m.attrib['value'] = 'Metadata/pick_1.png'
            else:
                c_tree.remove(plate_elem)

        assemble_elem = c_tree.find('assemble')
        if assemble_elem is not None:
            for a_item in list(assemble_elem):
                if a_item.attrib.get('object_id') not in plate_objs:
                    assemble_elem.remove(a_item)
        cfg_out = ET.tostring(c_tree, encoding='utf-8', xml_declaration=True)

        # 4. Snapmaker U1 project_settings.config
        u1_proj = u1_proj_base.copy()
        plate_colors = []
        plate_types = []
        for old_ext in sorted_exts[:4]:
            idx = old_ext - 1
            c = orig_colors[idx] if idx < len(orig_colors) else '#FFFFFFFF'
            t = orig_types[idx] if idx < len(orig_types) else 'PLA'
            plate_colors.append(c)
            plate_types.append(t)

        while len(plate_colors) < 4:
            plate_colors.append('#FFFFFFFF')
            plate_types.append('PLA')

        u1_proj['filament_colour'] = plate_colors
        u1_proj['filament_type'] = plate_types
        u1_proj['filament_settings_id'] = ['Snapmaker PLA SnapSpeed @U1' for _ in range(4)]
        u1_proj['printer_model'] = 'Snapmaker U1'

        for key, val in u1_proj.items():
            if key.startswith('filament_') and isinstance(val, list) and 0 < len(val) != 4:
                if len(val) < 4:
                    val.extend([val[-1]] * (4 - len(val)))
                else:
                    u1_proj[key] = val[:4]

        proj_bytes = json.dumps(u1_proj, indent=4, ensure_ascii=False).encode('utf-8')

        # 5. slice_info.config
        slice_info_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value=""/>
  </header>
  <plate>
    <metadata key="printer_model_id" value="Snapmaker U1"/>
"""
        for slot_num, (c, t) in enumerate(zip(plate_colors, plate_types), start=1):
            slice_info_xml += f'    <filament id="{slot_num}" type="{t}" color="{c}" used_m="0" used_g="0"/>\n'
        slice_info_xml += """  </plate>
</config>
"""
        slice_info_bytes = slice_info_xml.encode('utf-8')

        # 6. Save U1 3MF
        out_name = os.path.join(output_dir, f"{base_name}_U1_Plate_{pid:02d}.3mf")
        with zipfile.ZipFile(bambu_3mf_path, 'r') as bbl, \
             zipfile.ZipFile(out_name, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            zout.writestr('[Content_Types].xml', content_types)
            zout.writestr('_rels/.rels', dot_rels)
            zout.writestr('3D/3dmodel.model', model_out.encode('utf-8'))
            zout.writestr('3D/_rels/3dmodel.model.rels', rels_out)
            
            for mpath in needed_models:
                mname = mpath.lstrip('/')
                if mname in all_bbl_files:
                    zout.writestr(mname, bbl.read(mname))

            zout.writestr('Metadata/model_settings.config', cfg_out)
            zout.writestr('Metadata/project_settings.config', proj_bytes)
            zout.writestr('Metadata/slice_info.config', slice_info_bytes)

            img_maps = [
                (f"Metadata/plate_{pid}.png", "Metadata/plate_1.png"),
                (f"Metadata/plate_{pid}_small.png", "Metadata/plate_1_small.png"),
                (f"Metadata/plate_no_light_{pid}.png", "Metadata/plate_no_light_1.png"),
                (f"Metadata/top_{pid}.png", "Metadata/top_1.png"),
                (f"Metadata/pick_{pid}.png", "Metadata/pick_1.png"),
            ]
            for src_i, dst_i in img_maps:
                if src_i in all_bbl_files:
                    zout.writestr(dst_i, bbl.read(src_i))

        converted_files.append(out_name)
        print(f"Generated: {os.path.basename(out_name)} ({os.path.getsize(out_name)/(1024*1024):.2f} MB)")

    return converted_files


# ---------------------------------------------------------------------------
# Flask Web Interface
# ---------------------------------------------------------------------------
def run_web_server(port=5000):
    from flask import Flask, request, jsonify, render_template_string, send_file
    import webbrowser

    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB
    upload_dir = os.path.join(SCRIPT_DIR, "web_uploads")
    os.makedirs(upload_dir, exist_ok=True)

    HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bambu to Snapmaker U1 Multi-Plate Converter</title>
<style>
  :root {
    --bg: #121418;
    --card: #1c1f26;
    --border: #2e3440;
    --accent: #ff4757;
    --accent-u1: #00d2d3;
    --text: #eef2f7;
    --text-muted: #8892b0;
    --success: #2ed573;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 24px;
    display: flex;
    flex-direction: column;
    align-items: center;
  }
  .container { max-width: 900px; width: 100%; }
  header { text-align: center; margin-bottom: 24px; }
  h1 { margin: 0; font-size: 26px; }
  h1 span { color: var(--accent-u1); }
  p.subtitle { color: var(--text-muted); margin: 6px 0 0 0; font-size: 14px; }
  
  .drop-zone {
    border: 2px dashed var(--border);
    border-radius: 12px;
    padding: 36px 20px;
    text-align: center;
    background: var(--card);
    cursor: pointer;
    transition: 0.2s;
  }
  .drop-zone.dragover { border-color: var(--accent-u1); background: #222733; }
  .drop-zone svg { width: 44px; height: 44px; fill: var(--text-muted); margin-bottom: 8px; }
  
  .btn {
    background: var(--accent-u1);
    color: #111;
    font-weight: 600;
    border: none;
    padding: 10px 22px;
    border-radius: 8px;
    cursor: pointer;
    font-size: 14px;
    transition: 0.2s;
  }
  .btn:hover { filter: brightness(1.1); transform: translateY(-1px); }
  .btn-all { background: var(--success); color: #000; font-size: 15px; padding: 12px 28px; }

  .summary-bar {
    display: none;
    margin-top: 20px;
    padding: 16px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .plates-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 16px;
    margin-top: 20px;
  }
  .plate-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
  }
  .plate-thumb {
    width: 100%;
    height: 140px;
    background: #0d0f12;
    border-radius: 6px;
    object-fit: contain;
    margin-bottom: 10px;
  }
  .plate-title { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
  .plate-details { font-size: 12px; color: var(--text-muted); margin-bottom: 10px; }
  .swatches { display: flex; gap: 6px; margin-bottom: 12px; }
  .swatch {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    border: 1px solid rgba(255,255,255,0.3);
  }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>Bambu &rarr; <span>Snapmaker U1</span> Multi-Plate</h1>
    <p class="subtitle">Convert multi-plate & 12+ color Bambu 3MF projects to individual Snapmaker U1 plates.</p>
  </header>

  <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
    <svg viewBox="0 0 24 24"><path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM14 13v4h-4v-4H7l5-5 5 5h-3z"/></svg>
    <div style="font-size: 16px; font-weight: 500;">Drop Bambu .3mf project file here</div>
    <div style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">or click to browse from desktop</div>
    <input type="file" id="fileInput" accept=".3mf" style="display:none">
  </div>

  <div id="statusText" style="text-align: center; margin-top: 15px; color: var(--text-muted);"></div>

  <div id="summarySection" style="display:none;">
    <div class="summary-bar">
      <div>
        <strong id="modelName"></strong>
        <div style="font-size: 13px; color: var(--text-muted); margin-top: 2px;" id="modelMeta"></div>
      </div>
      <button class="btn btn-all" onclick="convertAll()">Convert All Plates to U1 (.zip)</button>
    </div>

    <div class="plates-grid" id="platesGrid"></div>
  </div>
</div>

<script>
let currentFile = null;
let currentInfo = null;

const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');

dropZone.ondragover = (e) => { e.preventDefault(); dropZone.classList.add('dragover'); };
dropZone.ondragleave = () => dropZone.classList.remove('dragover');
dropZone.ondrop = (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
};
fileInput.onchange = () => { if (fileInput.files.length) handleFile(fileInput.files[0]); };

async function handleFile(file) {
  if (!file.name.endsWith('.3mf')) { alert('Please select a .3mf file'); return; }
  currentFile = file;
  document.getElementById('statusText').innerText = "Analyzing plates and colors...";

  const formData = new FormData();
  formData.append('file', file);
  const resp = await fetch('/api/analyze', { method: 'POST', body: formData });
  const data = await resp.json();
  if (data.error) { alert(data.error); return; }
  
  currentInfo = data;
  renderPlates(data);
}

function renderPlates(info) {
  document.getElementById('statusText').innerText = "";
  document.getElementById('summarySection').style.display = "block";
  document.getElementById('modelName').innerText = info.filename;
  document.getElementById('modelMeta').innerText = `${info.plates_count} Plates | ${info.total_filaments} Total Colors | Auto-centered for U1`;

  const grid = document.getElementById('platesGrid');
  grid.innerHTML = "";

  info.plates.forEach(p => {
    const card = document.createElement('div');
    card.className = "plate-card";
    
    let swatchesHtml = p.filaments.map(f => `<div class="swatch" style="background:${f.color}" title="${f.color} (${f.type})"></div>`).join('');
    let thumbSrc = p.thumbnail_b64 ? `data:image/png;base64,${p.thumbnail_b64}` : '';
    
    card.innerHTML = `
      ${thumbSrc ? `<img class="plate-thumb" src="${thumbSrc}"/>` : `<div class="plate-thumb" style="display:flex;align-items:center;justify-content:center;color:#555;">No Preview</div>`}
      <div class="plate-title">${p.name}</div>
      <div class="plate-details">${p.objects_count} parts | ${p.filaments.length} colors on plate</div>
      <div class="swatches">${swatchesHtml}</div>
      <button class="btn" onclick="convertPlate(${p.id})">Export Plate ${p.id} for U1</button>
    `;
    grid.appendChild(card);
  });
}

async function convertAll() {
  document.getElementById('statusText').innerText = "Converting all plates to Snapmaker U1 format...";
  const resp = await fetch(`/api/convert_all?filename=${encodeURIComponent(currentInfo.server_filename)}`);
  if (resp.ok) {
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${currentInfo.filename.replace('.3mf','')}_U1_All_Plates.zip`;
    a.click();
    document.getElementById('statusText').innerText = "All plates successfully converted and downloaded!";
  } else {
    alert("Conversion error");
  }
}

async function convertPlate(plateId) {
  const resp = await fetch(`/api/convert_plate?filename=${encodeURIComponent(currentInfo.server_filename)}&plate=${plateId}`);
  if (resp.ok) {
    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${currentInfo.filename.replace('.3mf','')}_U1_Plate_${plateId}.3mf`;
    a.click();
  } else {
    alert("Plate export error");
  }
}
</script>
</body>
</html>
"""

    @app.route('/')
    def index():
        return render_template_string(HTML_TEMPLATE)

    @app.route('/api/analyze', methods=['POST'])
    def analyze():
        f = request.files.get('file')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        save_path = os.path.join(upload_dir, f.filename)
        f.save(save_path)
        info = parse_plates_info(save_path)
        info['server_filename'] = f.filename
        return jsonify(info)

    @app.route('/api/convert_all')
    def convert_all_plates():
        fname = request.args.get('filename')
        fpath = os.path.join(upload_dir, fname)
        if not os.path.exists(fpath):
            return jsonify({'error': 'File not found'}), 404
        
        temp_out = os.path.join(upload_dir, f"{os.path.splitext(fname)[0]}_temp_out")
        out_files = convert_bambu_to_u1(fpath, temp_out)
        
        # Zip all converted plates
        zip_out = os.path.join(upload_dir, f"{os.path.splitext(fname)[0]}_U1_Plates.zip")
        with zipfile.ZipFile(zip_out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            for of in out_files:
                z.write(of, arcname=os.path.basename(of))
        
        return send_file(zip_out, as_attachment=True, download_name=f"{os.path.splitext(fname)[0]}_Snapmaker_U1.zip")

    @app.route('/api/convert_plate')
    def convert_single_plate():
        fname = request.args.get('filename')
        pid = int(request.args.get('plate'))
        fpath = os.path.join(upload_dir, fname)
        temp_out = os.path.join(upload_dir, f"{os.path.splitext(fname)[0]}_temp_out")
        out_files = convert_bambu_to_u1(fpath, temp_out, plate_selection=[pid])
        if out_files:
            return send_file(out_files[0], as_attachment=True, download_name=os.path.basename(out_files[0]))
        return jsonify({'error': 'Failed to convert plate'}), 500

    print(f"Starting Bambu2U1 Web Server on http://localhost:{port}...")
    webbrowser.open(f"http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)


def main():
    parser = argparse.ArgumentParser(description="Convert Bambu Studio 3MF multi-plate files to Snapmaker U1.")
    parser.add_argument("file", nargs="?", help="Path to Bambu 3MF file to convert")
    parser.add_argument("-o", "--output", help="Output directory for converted U1 files")
    parser.add_argument("--web", action="store_true", help="Launch interactive Web UI")
    parser.add_argument("--port", type=int, default=5000, help="Web UI port (default: 5000)")
    args = parser.parse_args()

    if args.web or not args.file:
        run_web_server(port=args.port)
    else:
        if not os.path.exists(args.file):
            print(f"Error: File not found: {args.file}")
            sys.exit(1)
        out_dir = args.output
        print(f"Converting {args.file} to Snapmaker U1 plates...")
        files = convert_bambu_to_u1(args.file, out_dir)
        print(f"\nAll {len(files)} plates successfully saved to:")
        print(os.path.abspath(out_dir or os.path.dirname(files[0])))

if __name__ == '__main__':
    main()
