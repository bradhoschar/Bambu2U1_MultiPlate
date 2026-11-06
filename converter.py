#!/usr/bin/env python3
"""
Bambu2U1 Core Converter Engine - v1.1.0
Converts Bambu Studio / MakerWorld 3MF projects into Snapmaker U1-compatible 3MF files.
Preserves filament materials, temperatures, speeds, volumetric flows, custom gcode,
per-part and triangle paint colors, and dynamically centers plates on the U1 bed.
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


def remap_paint_color_string(paint_str, ext_mapping):
    """
    Remaps paint_color hex strings (e.g., '5C' -> slot mapping).
    In Bambu Studio 3MF, paint_color encodes filament indices in hex nibbles followed by run lengths.
    """
    if not paint_str or not ext_mapping:
        return paint_str

    pattern = re.compile(r'([0-9A-Fa-f]+)(C)?')
    res = []
    for m in pattern.finditer(paint_str):
        token_num_str = m.group(1)
        suffix = m.group(2) or ''
        try:
            val_int = int(token_num_str, 16)
            str_val = str(val_int)
            if str_val in ext_mapping:
                new_slot = int(ext_mapping[str_val])
                res.append(f"{new_slot:X}{suffix}")
            else:
                res.append(m.group(0))
        except ValueError:
            res.append(m.group(0))

    return "".join(res) if res else paint_str


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

        has_support = False
        diff = orig_proj.get('different_settings_to_system', [])
        for item in diff:
            if isinstance(item, str) and 'enable_support' in item:
                if 'enable_support = 1' in item or 'enable_support=1' in item:
                    has_support = True
                elif 'enable_support = 0' in item or 'enable_support=0' in item:
                    has_support = False
        if 'enable_support' in orig_proj:
            has_support = str(orig_proj['enable_support']).strip() in ('1', 'true', 'True')

        obj_info = {}
        for obj in cfg_root.findall('object'):
            oid = obj.attrib.get('id')
            meta = {m.attrib.get('key'): m.attrib.get('value') for m in obj.findall('metadata')}
            ext = int(meta.get('extruder', 1))
            
            part_exts = set()
            for p in obj.findall('part'):
                for pm in p.findall('metadata'):
                    if pm.attrib.get('key') == 'extruder':
                        try:
                            part_exts.add(int(pm.attrib.get('value')))
                        except:
                            pass
            
            obj_info[oid] = {
                'name': meta.get('name', f'Object_{oid}'),
                'extruder': ext,
                'part_extruders': part_exts
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
                    used_extruders.update(obj_info[oid]['part_extruders'])

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
    Preserves all source project print settings, material temps, speeds, and layer gcodes.
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

        custom_gcode_xml = bbl.read('Metadata/custom_gcode_per_layer.xml') if 'Metadata/custom_gcode_per_layer.xml' in all_bbl_files else None

        orig_proj = json.loads(bbl.read('Metadata/project_settings.config').decode('utf-8'))
        orig_colors = orig_proj.get('filament_colour', [])
        orig_types  = orig_proj.get('filament_type', [])

        has_support = False
        diff = orig_proj.get('different_settings_to_system', [])
        for item in diff:
            if isinstance(item, str) and 'enable_support' in item:
                if 'enable_support = 1' in item or 'enable_support=1' in item:
                    has_support = True
                elif 'enable_support = 0' in item or 'enable_support=0' in item:
                    has_support = False
        if 'enable_support' in orig_proj:
            has_support = str(orig_proj['enable_support']).strip() in ('1', 'true', 'True')

        u1_template = tmpl_supp if (has_support and os.path.exists(tmpl_supp)) else tmpl_no_supp

        with zipfile.ZipFile(u1_template, 'r') as u1:
            u1_proj_base = json.loads(u1.read('Metadata/project_settings.config').decode('utf-8'))

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

    plate_objs = set()
    for inst in target_plate.findall('model_instance'):
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
            for part in obj.findall('part'):
                for pm in part.findall('metadata'):
                    if pm.attrib.get('key') == 'extruder':
                        try:
                            used_extruders.add(int(pm.attrib.get('value')))
                        except:
                            pass

    sorted_exts = sorted(list(used_extruders))
    if not sorted_exts:
        sorted_exts = [1]

    ext_mapping = {}
    source_ext_for_slot = {}

    if custom_mapping:
        for old_str, conf in custom_mapping.items():
            new_s = int(conf.get('slot', 1))
            if 1 <= new_s <= 4:
                ext_mapping[old_str] = str(new_s)
                source_ext_for_slot[new_s - 1] = int(old_str) - 1
    else:
        for new_slot, old_ext in enumerate(sorted_exts[:4], start=1):
            ext_mapping[str(old_ext)] = str(new_slot)
            source_ext_for_slot[new_slot - 1] = old_ext - 1

    fallback_idx = source_ext_for_slot.get(0, 0)
    for s_idx in range(4):
        if s_idx not in source_ext_for_slot:
            source_ext_for_slot[s_idx] = fallback_idx

    ET.register_namespace('', NS_CORE)
    ET.register_namespace('p', NS_PROD)
    ET.register_namespace('BambuStudio', NS_BBL)
    m_tree = ET.fromstring(raw_model)

    build_elem = m_tree.find('m:build', NS_MAP)
    plate_items_tf = []
    for item in list(build_elem):
        oid = item.attrib.get('objectid')
        if oid in plate_objs:
            tf = [float(x) for x in item.attrib['transform'].split()]
            plate_items_tf.append((item, tf))
        else:
            build_elem.remove(item)

    if plate_items_tf:
        xs = [tf[9] for _, tf in plate_items_tf]
        ys = [tf[10] for _, tf in plate_items_tf]
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        dx = cx - 135.0
        dy = cy - 135.0
        for item, tf in plate_items_tf:
            tf[9] = round(tf[9] - dx, 6)
            tf[10] = round(tf[10] - dy, 6)
            item.attrib['transform'] = " ".join(str(v) for v in tf)
    else:
        dx, dy = 0.0, 0.0

    needed_models = set()
    res_elem = m_tree.find('m:resources', NS_MAP)
    for obj in list(res_elem):
        oid = obj.attrib.get('id')
        if oid in plate_objs:
            for comp in obj.findall('m:components/m:component', NS_MAP):
                p_path = comp.attrib.get(f'{{{NS_PROD}}}path')
                if p_path:
                    needed_models.add(p_path)
        else:
            res_elem.remove(obj)

    base_name = os.path.splitext(os.path.basename(bambu_3mf_path))[0]
    for meta in m_tree.findall('m:metadata', NS_MAP):
        if meta.attrib.get('name') == 'Title':
            meta.text = f"{base_name} - Plate {pid} (Snapmaker U1)"
        elif meta.attrib.get('name') == 'Thumbnail_Middle':
            meta.text = "/Metadata/plate_1.png"
        elif meta.attrib.get('name') == 'Thumbnail_Small':
            meta.text = "/Metadata/plate_1_small.png"

    model_out = ET.tostring(m_tree, encoding='utf-8', xml_declaration=True).decode('utf-8')
    if 'xmlns:BambuStudio' not in model_out:
        model_out = model_out.replace('<model ', f'<model xmlns:BambuStudio="{NS_BBL}" ', 1)

    ET.register_namespace('', NS_REL)
    r_tree = ET.fromstring(raw_rels)
    for rel in list(r_tree):
        if rel.attrib.get('Target') not in needed_models:
            r_tree.remove(rel)
    rels_out = ET.tostring(r_tree, encoding='utf-8', xml_declaration=True)

    c_tree = ET.fromstring(raw_cfg)
    for obj in list(c_tree.findall('object')):
        if obj.attrib.get('id') not in plate_objs:
            c_tree.remove(obj)
        else:
            for m in obj.findall('metadata'):
                if m.attrib.get('key') == 'extruder':
                    old_v = m.attrib.get('value')
                    m.attrib['value'] = ext_mapping.get(old_v, '1')
            for part in obj.findall('part'):
                for pm in part.findall('metadata'):
                    if pm.attrib.get('key') == 'extruder':
                        old_pv = pm.attrib.get('value')
                        pm.attrib['value'] = ext_mapping.get(old_pv, '1')

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

    final_proj = orig_proj.copy()

    machine_override_keys = [
        'printer_model', 'printer_settings_id', 'printer_technology', 'printer_structure',
        'printer_variant', 'printable_area', 'machine_start_gcode', 'machine_end_gcode',
        'machine_pause_gcode', 'emit_machine_limits_to_gcode', 'machine_tool_change_time',
        'machine_unload_filament_time', 'machine_load_filament_time', 'bed_custom_model',
        'bed_custom_texture', 'nozzle_type', 'machine_max_speed_e', 'machine_max_speed_x',
        'machine_max_speed_y', 'machine_max_speed_z', 'machine_max_acceleration_e',
        'machine_max_acceleration_extruding', 'machine_max_acceleration_retracting',
        'machine_max_acceleration_travel', 'machine_max_acceleration_x', 'machine_max_acceleration_y',
        'machine_max_acceleration_z', 'machine_max_jerk_e', 'machine_max_jerk_x',
        'machine_max_jerk_y', 'machine_max_jerk_z', 'machine_max_junction_deviation'
    ]
    for mk in machine_override_keys:
        if mk in u1_proj_base:
            final_proj[mk] = u1_proj_base[mk]

    for key, val in orig_proj.items():
        if key.startswith('filament_') or 'temp' in key or 'cooling' in key or 'fan' in key or 'flow' in key:
            if isinstance(val, list) and len(val) >= 1:
                new_list = []
                for s_idx in range(4):
                    orig_src_idx = source_ext_for_slot[s_idx]
                    if orig_src_idx < len(val):
                        new_list.append(val[orig_src_idx])
                    else:
                        new_list.append(val[-1])
                final_proj[key] = new_list

    slot_colors = [final_proj.get('filament_colour', ['#FFFFFFFF']*4)[i] for i in range(4)]
    slot_types  = [final_proj.get('filament_type', ['PLA']*4)[i] for i in range(4)]

    proj_bytes = json.dumps(final_proj, indent=4, ensure_ascii=False).encode('utf-8')

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
                raw_comp = bbl.read(safe_name)
                try:
                    comp_root = ET.fromstring(raw_comp)
                    modified_comp = False
                    for el in comp_root.iter():
                        if 'paint_color' in el.attrib:
                            el.attrib['paint_color'] = remap_paint_color_string(el.attrib['paint_color'], ext_mapping)
                            modified_comp = True
                    if modified_comp:
                        raw_comp = ET.tostring(comp_root, encoding='utf-8', xml_declaration=True)
                except Exception:
                    pass
                zout.writestr(safe_name, raw_comp)

        zout.writestr('Metadata/model_settings.config', cfg_out)
        zout.writestr('Metadata/project_settings.config', proj_bytes)
        zout.writestr('Metadata/slice_info.config', slice_info_bytes)

        if custom_gcode_xml:
            zout.writestr('Metadata/custom_gcode_per_layer.xml', custom_gcode_xml)

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
    parser = argparse.ArgumentParser(description="Convert Bambu Studio 3MF to Snapmaker U1 - v1.1")
    parser.add_argument("file", help="Path to Bambu .3mf project file")
    parser.add_argument("-o", "--output", help="Output directory (default: [filename]_U1_Ready)")
    args = parser.parse_args()

    file_path = os.path.abspath(args.file)
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    out_dir = os.path.abspath(args.output) if args.output else os.path.join(os.path.dirname(file_path), f"{os.path.splitext(os.path.basename(file_path))[0]}_U1_Ready")
    tmpl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

    print(f"Converting '{os.path.basename(file_path)}' to Snapmaker U1 (v1.1 Engine)...")
    def print_progress(idx, total, fpath):
        print(f"  [{idx}/{total}] Generated {os.path.basename(fpath)}")

    results = convert_all_plates(file_path, out_dir, tmpl_dir, progress_callback=print_progress)
    print(f"\nAll {len(results)} plates successfully converted to:\n{out_dir}")
