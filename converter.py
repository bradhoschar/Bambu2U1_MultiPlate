#!/usr/bin/env python3
"""
Bambu2U1 Core Converter Engine
Converts Bambu Studio / MakerWorld 3MF projects into Snapmaker U1-compatible 3MF files.
Supports multi-plate projects, 12+ color setups, auto-centering on the U1 bed,
and fixes prime tower variable layer height errors.
"""

import os
import re
import json
import base64
import zipfile
import posixpath
import xml.etree.ElementTree as ET

NS_CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
NS_PROD = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'
NS_REL  = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS_BBL  = 'http://schemas.bambulab.com/package/2021'
NS_MAP  = {'m': NS_CORE, 'p': NS_PROD}


def parse_bambu_project(bambu_3mf_path):
    """
    Parses a Bambu Lab / MakerWorld 3MF file.
    Returns metadata about plates, parts, filaments, and support settings.
    """
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

        # Build object lookup: id -> name, extruder
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
                plate_filaments.append({
                    'original_slot': ext,
                    'color': c,
                    'type': t
                })

            # Retrieve plate preview thumbnail
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


def convert_plate_to_u1(bambu_3mf_path, pid, output_3mf_path, templates_dir, custom_mapping=None):
    """
    Converts a single plate from a Bambu 3MF file to a Snapmaker U1 ready 3MF file.
    custom_mapping: optional dict mapping str(original_ext) -> {'slot': int, 'color': str, 'type': str}
    """
    tmpl_no_supp = os.path.join(templates_dir, 'u1_template.3mf')
    tmpl_supp    = os.path.join(templates_dir, 'u1_template_supports.3mf')

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

    # Find the target plate
    target_plate = None
    for p in cfg_root.findall('plate'):
        for m in p.findall('metadata'):
            if m.attrib.get('key') == 'plater_id' and int(m.attrib.get('value')) == pid:
                target_plate = p
                break
        if target_plate is not None:
            break

    if target_plate is None:
        raise ValueError(f"Plate {pid} not found in {bambu_3mf_path}")

    # Calculate grid offset for centering
    col = (pid - 1) % 4
    row = (pid - 1) // 4
    dx = col * 340.0
    dy = row * -320.0

    # Collect object IDs on this plate
    plate_objs = set()
    for inst in target_plate.findall('model_instance'):
        for m in inst.findall('metadata'):
            if m.attrib['key'] == 'object_id':
                plate_objs.add(m.attrib['value'])

    # Determine extruders used on this plate
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

    # Build slot mapping
    # ext_mapping: str(old_ext) -> str(new_slot_1_to_4)
    ext_mapping = {}
    slot_colors = ['#FFFFFFFF'] * 4
    slot_types  = ['PLA'] * 4

    if custom_mapping:
        for old_str, conf in custom_mapping.items():
            new_s = int(conf.get('slot', 1))
            if 1 <= new_s <= 4:
                ext_mapping[old_str] = str(new_s)
                slot_colors[new_s - 1] = conf.get('color', '#FFFFFFFF')
                slot_types[new_s - 1]  = conf.get('type', 'PLA')
    else:
        for new_slot, old_ext in enumerate(sorted_exts[:4], start=1):
            ext_mapping[str(old_ext)] = str(new_slot)
            idx = old_ext - 1
            c = orig_colors[idx] if idx < len(orig_colors) else '#FFFFFFFF'
            t = orig_types[idx] if idx < len(orig_types) else 'PLA'
            slot_colors[new_slot - 1] = c
            slot_types[new_slot - 1]  = t

    # 1. Build 3D/3dmodel.model
    ET.register_namespace('', NS_CORE)
    ET.register_namespace('p', NS_PROD)
    ET.register_namespace('BambuStudio', NS_BBL)
    m_tree = ET.fromstring(raw_model)

    base_name = os.path.splitext(os.path.basename(bambu_3mf_path))[0]
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

    # 2. Build 3D/_rels/3dmodel.model.rels
    ET.register_namespace('', NS_REL)
    r_tree = ET.fromstring(raw_rels)
    for rel in list(r_tree):
        if rel.attrib.get('Target') not in needed_models:
            r_tree.remove(rel)
    rels_out = ET.tostring(r_tree, encoding='utf-8', xml_declaration=True)

    # 3. Build Metadata/model_settings.config
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
    u1_proj['filament_colour'] = slot_colors
    u1_proj['filament_type']   = slot_types
    u1_proj['filament_settings_id'] = ['Snapmaker PLA SnapSpeed @U1'] * 4
    u1_proj['printer_model'] = 'Snapmaker U1'

    for key, val in u1_proj.items():
        if key.startswith('filament_') and isinstance(val, list) and 0 < len(val) != 4:
            if len(val) < 4:
                val.extend([val[-1]] * (4 - len(val)))
            else:
                u1_proj[key] = val[:4]

    proj_bytes = json.dumps(u1_proj, indent=4, ensure_ascii=False).encode('utf-8')

    # 5. Build Metadata/slice_info.config
    slice_info_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value=""/>
  </header>
  <plate>
    <metadata key="printer_model_id" value="Snapmaker U1"/>
"""
    for slot_num, (c, t) in enumerate(zip(slot_colors, slot_types), start=1):
        slice_info_xml += f'    <filament id="{slot_num}" type="{t}" color="{c}" used_m="0" used_g="0"/>\n'
    slice_info_xml += """  </plate>
</config>
"""
    slice_info_bytes = slice_info_xml.encode('utf-8')

    # 6. Write final U1 3MF archive (sanitizing paths against Zip Slip)
    os.makedirs(os.path.dirname(os.path.abspath(output_3mf_path)), exist_ok=True)
    with zipfile.ZipFile(bambu_3mf_path, 'r') as bbl, \
         zipfile.ZipFile(output_3mf_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:

        zout.writestr('[Content_Types].xml', content_types)
        zout.writestr('_rels/.rels', dot_rels)
        zout.writestr('3D/3dmodel.model', model_out.encode('utf-8'))
        zout.writestr('3D/_rels/3dmodel.model.rels', rels_out)

        for mpath in needed_models:
            safe_name = posixpath.normpath(mpath).lstrip('/')
            if safe_name in all_bbl_files:
                zout.writestr(safe_name, bbl.read(safe_name))

        zout.writestr('Metadata/model_settings.config', cfg_out)
        zout.writestr('Metadata/project_settings.config', proj_bytes)
        zout.writestr('Metadata/slice_info.config', slice_info_bytes)

        # Image previews
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

    return output_3mf_path


def convert_all_plates(bambu_3mf_path, output_dir, templates_dir, progress_callback=None):
    """Converts all plates found in a Bambu 3MF file to Snapmaker U1."""
    meta = parse_bambu_project(bambu_3mf_path)
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(bambu_3mf_path))[0]

    output_files = []
    total = len(meta['plates'])
    for idx, plate in enumerate(meta['plates'], start=1):
        pid = plate['id']
        out_file = os.path.join(output_dir, f"{base_name}_U1_Plate_{pid:02d}.3mf")
        convert_plate_to_u1(bambu_3mf_path, pid, out_file, templates_dir)
        output_files.append(out_file)
        if progress_callback:
            progress_callback(idx, total, out_file)

    return output_files


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Convert Bambu Studio 3MF to Snapmaker U1")
    parser.add_argument("file", help="Path to Bambu .3mf project file")
    parser.add_argument("-o", "--output", help="Output directory (default: [filename]_U1_Ready)")
    args = parser.parse_args()

    file_path = os.path.abspath(args.file)
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    out_dir = os.path.abspath(args.output) if args.output else os.path.join(os.path.dirname(file_path), f"{os.path.splitext(os.path.basename(file_path))[0]}_U1_Ready")
    tmpl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

    print(f"Converting '{os.path.basename(file_path)}' to Snapmaker U1...")
    def print_progress(idx, total, fpath):
        print(f"  [{idx}/{total}] Generated {os.path.basename(fpath)}")

    results = convert_all_plates(file_path, out_dir, tmpl_dir, progress_callback=print_progress)
    print(f"\nAll {len(results)} plates saved to:\n{out_dir}")
