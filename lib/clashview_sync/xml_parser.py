# -*- coding: utf-8 -*-
"""XML Parser for Navisworks Clash Detective and Saved Viewpoints exports.

Parses:
1. Standard Navisworks Clash Detective XML (<batchtest>)
   - Tests, clash results, nested clash groups
   - Clash points, distances, statuses, descriptions
   - Clashing participants (Item 1 & Item 2)
   - Precise Revit Element IDs without inferring from unrelated fields
   - Model names, paths, selection tree node hierarchies, smarttags
   - Declared source units (<exchange units="..."> or <batchtest units="...">)
2. Saved Viewpoints XML (<viewpoints>)
   - Views, folders, camera projection (ortho / persp)
   - Camera eye position, rotation quaternions, clip planes
3. Ambiguity-safe viewpoint merger
   - Matches viewpoints to clash results via GUID or name
   - Flags ambiguous multiple matches rather than silently guessing

Compatible with Python 2.7 (pyRevit IronPython) and Python 3.x.
"""
from __future__ import absolute_import, division, print_function

import io
import os
import re
import sys
import xml.etree.ElementTree as ET

from .coordinate_transformer import (
    Quaternion,
    Vector3,
    validate_and_orthogonalize_camera,
)
from .models import (
    CameraViewpoint,
    ClashGroup,
    ClashParticipant,
    ClashPoint,
    ClashResultItem,
    ClashSyncPayload,
    ClashTestGroup,
    integer_types,
    string_types,
)


def _strip_ns(tag):
    """Strips XML namespace prefix like '{http://...}element' returning 'element'."""
    if tag and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _get_child_by_local_name(elem, local_name):
    """Finds first immediate child whose local tag name matches local_name."""
    if elem is None:
        return None
    target = local_name.lower()
    for child in elem:
        if _strip_ns(child.tag).lower() == target:
            return child
    return None


def _get_children_by_local_name(elem, local_name):
    """Finds all immediate children whose local tag name matches local_name."""
    if elem is None:
        return []
    target = local_name.lower()
    return [child for child in elem if _strip_ns(child.tag).lower() == target]


def _get_child_text(elem, local_name, default=None):
    """Returns trimmed text of a child element, or default if missing/empty."""
    child = _get_child_by_local_name(elem, local_name)
    if child is not None and child.text:
        text = child.text.strip()
        if text:
            return text
    return default


def _parse_float(val, default=None):
    """Safely converts string to float, returning default on failure."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _parse_pos3f(elem, units="m"):
    """Parses <pos3f x="..." y="..." z="..."/> into a ClashPoint.

    Searches either directly on elem if it is pos3f, or in its first pos3f child.
    """
    if elem is None:
        return None

    pos_elem = elem
    if _strip_ns(elem.tag).lower() != "pos3f":
        pos_elem = _get_child_by_local_name(elem, "pos3f")
        if pos_elem is None:
            return None

    x_val = _parse_float(pos_elem.get("x"))
    y_val = _parse_float(pos_elem.get("y"))
    z_val = _parse_float(pos_elem.get("z"))

    if x_val is None or y_val is None or z_val is None:
        return None

    return ClashPoint(x=x_val, y=y_val, z=z_val, units=units)


def _parse_element_id(raw_str):
    """Strictly parses a candidate Element ID string.

    Returns:
      (element_id_int_or_none, raw_str_or_none, diagnostic_warning_or_none)
    """
    if raw_str is None:
        return (None, None, None)

    cleaned = str(raw_str).strip()
    if not cleaned:
        return (None, None, None)

    try:
        int_val = int(cleaned)
        if int_val > 0:
            return (int_val, cleaned, None)
        else:
            return (
                int_val,
                cleaned,
                "Element ID {0} is non-positive or invalid.".format(int_val),
            )
    except (ValueError, TypeError):
        return (
            None,
            cleaned,
            "Element ID '{0}' could not be parsed as an integer.".format(cleaned),
        )


def parse_clash_participant(clashobject_elem, model_units="m"):
    """Parses a <clashobject> element into a ClashParticipant instance."""
    diagnostics = []

    # 1. Layer
    layer = _get_child_text(clashobject_elem, "layer")

    # 2. Pathlink nodes (selection tree hierarchy)
    path_nodes = []
    pathlink_elem = _get_child_by_local_name(clashobject_elem, "pathlink")
    if pathlink_elem is not None:
        for node in _get_children_by_local_name(pathlink_elem, "node"):
            if node.text and node.text.strip():
                path_nodes.append(node.text.strip())

    # 3. Object attributes (<objectattribute><name>...</name><value>...</value>)
    attributes = {}
    for attr_elem in _get_children_by_local_name(clashobject_elem, "objectattribute"):
        name = _get_child_text(attr_elem, "name")
        val = _get_child_text(attr_elem, "value")
        if name and val:
            attributes[name] = val

    # 4. Smarttags (<smarttags><smarttag><name>...</name><value>...</value>)
    smarttags = {}
    smarttags_elem = _get_child_by_local_name(clashobject_elem, "smarttags")
    if smarttags_elem is not None:
        for tag_elem in _get_children_by_local_name(smarttags_elem, "smarttag"):
            name = _get_child_text(tag_elem, "name")
            val = _get_child_text(tag_elem, "value")
            if name and val:
                smarttags[name] = val

    # 5. Extract Model Name and Path
    model_name = None
    model_path = None

    # Check smarttags first
    for k, v in smarttags.items():
        k_lower = k.lower()
        if "file name" in k_lower or "model name" in k_lower:
            model_name = v
        elif "item path" in k_lower or "file path" in k_lower or "source file" in k_lower:
            model_path = v

    # Fallback to root node in pathlink
    if not model_name and path_nodes:
        model_name = path_nodes[0]

    # Fallback to layer if it ends with .nwc/.rvt
    if not model_name and layer and (layer.lower().endswith(".nwc") or layer.lower().endswith(".rvt")):
        model_name = layer

    # 6. Extract Item Name and Category
    item_name = smarttags.get("Item Name") or attributes.get("Name")
    if not item_name and path_nodes:
        item_name = path_nodes[-1]

    category = (
        smarttags.get("Category")
        or attributes.get("Category")
        or smarttags.get("Item Type")
    )
    if not category and len(path_nodes) >= 2:
        # Often node[1] or node[-2] is category
        category = path_nodes[1]

    # 7. Extract Element ID strictly
    # Look for explicit Element ID key in attributes or smarttags
    raw_elem_id = None
    for k, v in attributes.items():
        k_clean = k.strip().lower()
        if k_clean in ("element id", "elementid", "element_id"):
            raw_elem_id = v
            break

    if raw_elem_id is None:
        for k, v in smarttags.items():
            k_clean = k.strip().lower()
            if k_clean in ("element id", "elementid", "element_id"):
                raw_elem_id = v
                break

    element_id, raw_str, id_diag = _parse_element_id(raw_elem_id)
    if id_diag:
        diagnostics.append(id_diag)

    # Do not infer missing Element IDs from unrelated properties like GUID or Entity Handle
    unique_id = attributes.get("GUID") or attributes.get("UniqueId")

    return ClashParticipant(
        model_name=model_name,
        model_path=model_path,
        element_id=element_id,
        element_id_raw=raw_str,
        unique_id=unique_id,
        item_name=item_name,
        category=category,
        layer=layer,
        path_nodes=path_nodes,
        smarttags=smarttags,
        attributes=attributes,
        diagnostics=diagnostics,
    )


def parse_clash_result(
    clashresult_elem,
    model_units="m",
    parent_group=None,
    parent_group_guid=None,
):
    """Parses a <clashresult> element into a ClashResultItem instance."""
    diagnostics = []

    name = clashresult_elem.get("name")
    guid = clashresult_elem.get("guid")
    status = clashresult_elem.get("status") or _get_child_text(
        clashresult_elem, "resultstatus", default="New"
    )
    distance = _parse_float(clashresult_elem.get("distance"), default=0.0)
    description = _get_child_text(clashresult_elem, "description")

    # Clash Point
    clashpoint_elem = _get_child_by_local_name(clashresult_elem, "clashpoint")
    clash_point = _parse_pos3f(clashpoint_elem, units=model_units)
    if clash_point is None:
        diagnostics.append("Clash result has no valid clashpoint coordinates.")

    # Participants in <clashobjects>
    participants = []
    clashobjects_elem = _get_child_by_local_name(clashresult_elem, "clashobjects")
    if clashobjects_elem is not None:
        for obj_elem in _get_children_by_local_name(clashobjects_elem, "clashobject"):
            participant = parse_clash_participant(obj_elem, model_units=model_units)
            participants.append(participant)

    participant_a = participants[0] if len(participants) > 0 else None
    participant_b = participants[1] if len(participants) > 1 else None

    if len(participants) == 0:
        diagnostics.append("Clash result has 0 participants.")
    elif len(participants) == 1:
        diagnostics.append("Clash result has only 1 participant (missing second item).")
    elif len(participants) > 2:
        diagnostics.append(
            "Clash result has {0} participants; only first two are mapped to A and B.".format(
                len(participants)
            )
        )

    # Comments
    comments = []
    comments_elem = _get_child_by_local_name(clashresult_elem, "comments")
    if comments_elem is not None:
        for comm in _get_children_by_local_name(comments_elem, "comment"):
            user = _get_child_text(comm, "user")
            body = _get_child_text(comm, "body")
            date_str = _get_child_text(comm, "date")
            status_str = comm.get("status")
            comments.append(
                {
                    "user": user,
                    "body": body,
                    "date": date_str,
                    "status": status_str,
                }
            )

    return ClashResultItem(
        name=name,
        guid=guid,
        status=status,
        distance=distance,
        description=description,
        parent_group=parent_group,
        parent_group_guid=parent_group_guid,
        clash_point=clash_point,
        participant_a=participant_a,
        participant_b=participant_b,
        all_participants=participants,
        viewpoint=None,
        comments=comments,
        diagnostics=diagnostics,
    )


def parse_clash_group(clashgroup_elem, model_units="m"):
    """Parses a <clashgroup> element into a ClashGroup containing child clash results."""
    diagnostics = []

    name = clashgroup_elem.get("name")
    guid = clashgroup_elem.get("guid")
    status = clashgroup_elem.get("status") or _get_child_text(
        clashgroup_elem, "resultstatus", default="New"
    )
    description = _get_child_text(clashgroup_elem, "description")

    clashpoint_elem = _get_child_by_local_name(clashgroup_elem, "clashpoint")
    clash_point = _parse_pos3f(clashpoint_elem, units=model_units)

    results = []
    # Search for child clashresults
    clashresults_container = _get_child_by_local_name(clashgroup_elem, "clashresults")
    if clashresults_container is not None:
        for cr_elem in _get_children_by_local_name(clashresults_container, "clashresult"):
            result_item = parse_clash_result(
                cr_elem,
                model_units=model_units,
                parent_group=name,
                parent_group_guid=guid,
            )
            results.append(result_item)
    else:
        # Some schema variants put clashresult directly inside clashgroup
        for cr_elem in _get_children_by_local_name(clashgroup_elem, "clashresult"):
            result_item = parse_clash_result(
                cr_elem,
                model_units=model_units,
                parent_group=name,
                parent_group_guid=guid,
            )
            results.append(result_item)

    if not results:
        diagnostics.append("Clash group '{0}' contains 0 clash results.".format(name or ""))

    return ClashGroup(
        name=name,
        guid=guid,
        status=status,
        description=description,
        clash_point=clash_point,
        results=results,
        diagnostics=diagnostics,
    )


def parse_clash_test(clashtest_elem, model_units="m"):
    """Parses a <clashtest> element into a ClashTestGroup instance."""
    diagnostics = []

    name = clashtest_elem.get("name")
    guid = clashtest_elem.get("guid")
    test_type = clashtest_elem.get("test_type", "hard")
    status = clashtest_elem.get("status", "Active")
    tolerance = _parse_float(clashtest_elem.get("tolerance"), default=0.001)

    flat_results = []
    groups = []

    clashresults_elem = _get_child_by_local_name(clashtest_elem, "clashresults")
    if clashresults_elem is not None:
        for child in clashresults_elem:
            tag_name = _strip_ns(child.tag).lower()
            if tag_name == "clashresult":
                res = parse_clash_result(child, model_units=model_units)
                flat_results.append(res)
            elif tag_name == "clashgroup":
                grp = parse_clash_group(child, model_units=model_units)
                groups.append(grp)
                # Flat list includes all grouped results as well
                for grouped_res in grp.results:
                    flat_results.append(grouped_res)

    if not flat_results and not groups:
        diagnostics.append("Clash test '{0}' contains no clash results or groups.".format(name or ""))

    return ClashTestGroup(
        name=name,
        guid=guid,
        test_type=test_type,
        tolerance=tolerance,
        status=status,
        results=flat_results,
        groups=groups,
        diagnostics=diagnostics,
    )


def _parse_xml_string_safe(text):
    """Safely parses an XML string or byte sequence across CPython 3.x and IronPython 2.7.

    Prevents double-encoding UTF-8 byte strings while ensuring Unicode strings containing
    non-ASCII characters (e.g., Arabic, accented Latin) are properly UTF-8 encoded for
    IronPython 2.7's pyexpat parser.
    """
    if sys.version_info[0] >= 3:
        if isinstance(text, bytes):
            if text.startswith(b"\xef\xbb\xbf"):
                text = text[3:]
            return ET.fromstring(text.strip())
        cleaned = text.lstrip(u"\ufeff").strip()
        return ET.fromstring(cleaned)
    else:
        # Python 2 / IronPython 2.7
        if text.startswith("\xef\xbb\xbf"):
            text = text[3:]
        elif text.startswith(u"\ufeff"):
            text = text[1:]
        cleaned = text.strip()

        # Check if cleaned is already a UTF-8 byte sequence (all codepoints <= 255 forming valid UTF-8)
        is_utf8_bytes = False
        try:
            raw_latin1 = cleaned.encode("latin-1")
            if any(ord(ch) > 127 for ch in cleaned):
                raw_latin1.decode("utf-8")
                is_utf8_bytes = True
        except Exception:
            is_utf8_bytes = False

        if is_utf8_bytes:
            return ET.fromstring(cleaned)
        return ET.fromstring(cleaned.encode("utf-8"))


def _load_xml_root(source):
    """Loads XML ElementTree root from file path, string, bytes, or stream.

    Returns (root_elem, error_string_or_none)
    """
    if source is None:
        return (None, "XML source is None.")

    try:
        if sys.version_info[0] >= 3 and isinstance(source, bytes):
            return (_parse_xml_string_safe(source), None)

        # Check if source is a string or file path
        if isinstance(source, string_types):
            cleaned = source.strip()
            if cleaned.startswith("\xef\xbb\xbf"):
                cleaned = cleaned[3:].strip()
            elif cleaned.startswith(u"\ufeff"):
                cleaned = cleaned[1:].strip()

            # If it starts with '<', treat as XML string content
            if cleaned.startswith("<"):
                root = _parse_xml_string_safe(cleaned)
                return (root, None)
            elif os.path.isfile(source):
                tree = ET.parse(source)
                return (tree.getroot(), None)
            else:
                # Attempt to parse as raw XML string snippet
                root = _parse_xml_string_safe(cleaned)
                return (root, None)
        elif hasattr(source, "read"):
            tree = ET.parse(source)
            return (tree.getroot(), None)
        else:
            return (None, "Unsupported XML source type: {0}".format(type(source)))
    except ET.ParseError as pe:
        return (None, "XML ParseError: {0}".format(pe))
    except Exception as ex:
        return (None, "Failed to load XML: {0}".format(str(ex)))


def parse_clash_xml(source):
    """Parses a Navisworks Clash Detective XML file or XML string.

    Returns a ClashSyncPayload object with parsed tests, results, groups,
    and diagnostics.
    """
    diagnostics = []
    root, err = _load_xml_root(source)

    source_path = source if isinstance(source, string_types) and os.path.isfile(source) else None

    if err or root is None:
        fatal_diag = err or "Failed to parse XML root."
        return ClashSyncPayload(
            source_file=source_path,
            source_format="clash_xml",
            units="m",
            tests=[],
            diagnostics=[fatal_diag],
            units_verified=False,
        )

    # 1. Determine model units
    # Look at root (<exchange units="..."> or <batchtest units="...">)
    units = root.get("units")
    batchtest_elem = root
    if _strip_ns(root.tag).lower() != "batchtest":
        batchtest_elem = _get_child_by_local_name(root, "batchtest")
        if batchtest_elem is not None and not units:
            units = batchtest_elem.get("units")

    if not units:
        units = "m"
        units_verified = False
        diagnostics.append("No explicit units declared in XML; defaulted to meters ('m').")
    else:
        units = units.strip().lower()
        units_verified = True

    if batchtest_elem is None:
        # Check if clashtests or clashtest are direct children
        batchtest_elem = root

    # 2. Extract clashtests
    clashtests_container = _get_child_by_local_name(batchtest_elem, "clashtests")
    test_elems = []
    if clashtests_container is not None:
        test_elems = _get_children_by_local_name(clashtests_container, "clashtest")
    else:
        test_elems = _get_children_by_local_name(batchtest_elem, "clashtest")

    if not test_elems:
        diagnostics.append("No <clashtest> elements found in XML source.")

    tests = []
    for test_elem in test_elems:
        test_group = parse_clash_test(test_elem, model_units=units)
        tests.append(test_group)

    return ClashSyncPayload(
        source_file=source_path,
        source_format="clash_xml",
        units=units,
        tests=tests,
        viewpoints=[],
        diagnostics=diagnostics,
        units_verified=units_verified,
    )


def _parse_camera_viewpoint(view_elem, default_units="m", units_verified=True):
    """Parses a <view> element containing <viewpoint><camera> into CameraViewpoint."""
    name = view_elem.get("name")
    guid = view_elem.get("guid")

    vp_elem = _get_child_by_local_name(view_elem, "viewpoint")
    if vp_elem is None:
        return None

    focal_distance = _parse_float(vp_elem.get("focal"))
    fov = _parse_float(vp_elem.get("fov"))

    cam_elem = _get_child_by_local_name(vp_elem, "camera")
    if cam_elem is None:
        return None

    projection = cam_elem.get("projection", "persp")
    near_clip = _parse_float(cam_elem.get("near"))
    far_clip = _parse_float(cam_elem.get("far"))
    aspect = _parse_float(cam_elem.get("aspect"))
    view_height = _parse_float(cam_elem.get("height"))

    # Eye position
    pos_elem = _get_child_by_local_name(cam_elem, "position")
    position = _parse_pos3f(pos_elem, units=default_units)

    # Rotation quaternion
    quaternion = None
    rot_elem = _get_child_by_local_name(cam_elem, "rotation")
    if rot_elem is not None:
        quat_elem = _get_child_by_local_name(rot_elem, "quaternion")
        if quat_elem is not None:
            a = _parse_float(quat_elem.get("a"), 0.0)
            b = _parse_float(quat_elem.get("b"), 0.0)
            c = _parse_float(quat_elem.get("c"), 0.0)
            d = _parse_float(quat_elem.get("d"), 1.0)
            quaternion = (a, b, c, d)

    # Derived camera vectors
    forward_vector = None
    up_vector = None

    if quaternion:
        q_obj = Quaternion(quaternion[0], quaternion[1], quaternion[2], quaternion[3])
        f_vec, u_vec, r_vec = q_obj.to_camera_vectors()
        forward_vector = f_vec.to_tuple()
        up_vector = u_vec.to_tuple()

    # If explicit <up> vector is in viewpoint, validate/override
    up_elem = _get_child_by_local_name(vp_elem, "up")
    if up_elem is not None:
        explicit_up = _parse_pos3f(up_elem, units=default_units)
        if explicit_up:
            up_vector = explicit_up.to_tuple()

    return CameraViewpoint(
        name=name,
        guid=guid,
        projection=projection,
        position=position,
        quaternion=quaternion,
        up_vector=up_vector,
        forward_vector=forward_vector,
        focal_distance=focal_distance,
        fov=fov,
        aspect=aspect,
        view_height=view_height,
        near_clip=near_clip,
        far_clip=far_clip,
        units=default_units,
        units_verified=units_verified,
    )


def _collect_views_recursive(container_elem, default_units="m", results=None, units_verified=True):
    """Recursively traverses <viewpoints> and <viewfolder> to collect all <view> elements."""
    if results is None:
        results = []

    for child in container_elem:
        tag_clean = _strip_ns(child.tag).lower()
        if tag_clean == "view":
            vp = _parse_camera_viewpoint(
                child, default_units=default_units, units_verified=units_verified
            )
            if vp is not None:
                results.append(vp)
        elif tag_clean == "viewfolder":
            _collect_views_recursive(
                child,
                default_units=default_units,
                results=results,
                units_verified=units_verified,
            )

    return results


def parse_viewpoints_xml(source):
    """Parses a Navisworks Saved Viewpoints XML file or string.

    Returns a ClashSyncPayload with source_format='viewpoints_xml' and
    the list of parsed CameraViewpoint objects.
    """
    diagnostics = []
    root, err = _load_xml_root(source)

    source_path = source if isinstance(source, string_types) and os.path.isfile(source) else None

    if err or root is None:
        fatal_diag = err or "Failed to parse viewpoints XML root."
        return ClashSyncPayload(
            source_file=source_path,
            source_format="viewpoints_xml",
            units="m",
            viewpoints=[],
            diagnostics=[fatal_diag],
            units_verified=False,
        )

    # Find <viewpoints> root or child
    vp_container = root
    if _strip_ns(root.tag).lower() != "viewpoints":
        vp_container = _get_child_by_local_name(root, "viewpoints")
        if vp_container is None:
            vp_container = root

    # Check root and child elements for explicit units
    units = root.get("units")
    if not units and vp_container is not None and vp_container is not root:
        units = vp_container.get("units")
    if not units:
        bt_child = _get_child_by_local_name(root, "batchtest")
        if bt_child is not None:
            units = bt_child.get("units")

    if not units:
        units = "m"
        units_verified = False
        diagnostics.append(
            "No explicit units declared in viewpoints XML; defaulted to meters ('m') (unverified)."
        )
    else:
        units = units.strip().lower()
        units_verified = True

    viewpoints = _collect_views_recursive(
        vp_container, default_units=units, units_verified=units_verified
    )

    if not viewpoints:
        diagnostics.append("No saved viewpoints found in the provided XML source.")

    return ClashSyncPayload(
        source_file=source_path,
        source_format="viewpoints_xml",
        units=units,
        tests=[],
        viewpoints=viewpoints,
        diagnostics=diagnostics,
        units_verified=units_verified,
    )


def merge_clash_and_viewpoints(clash_payload, viewpoints_payload):
    """Safely pairs saved viewpoints to clash results.

    Matching strategy:
      1. Exact GUID match (unique)
      2. Exact Name match (case-sensitive, then case-insensitive)
      3. Ambiguity safety:
         If multiple viewpoints match a single clash result, do NOT silently
         pick one; record an ambiguity diagnostic and leave viewpoint unset.

    Returns the mutated clash_payload for chaining.
    """
    if not viewpoints_payload or not viewpoints_payload.viewpoints:
        clash_payload.diagnostics.append(
            "No viewpoints provided to merge with clash results."
        )
        return clash_payload

    # Build lookup tables
    by_guid = {}
    by_name = {}

    for vp in viewpoints_payload.viewpoints:
        if vp.guid:
            guid_key = vp.guid.strip().lower()
            if guid_key not in by_guid:
                by_guid[guid_key] = []
            by_guid[guid_key].append(vp)

        if vp.name:
            name_key = vp.name.strip()
            if name_key not in by_name:
                by_name[name_key] = []
            by_name[name_key].append(vp)

    matched_count = 0
    ambiguous_count = 0

    # Iterate over all tests and all clash results
    for test in clash_payload.tests:
        for clash in test.results:
            candidates = []

            # 1. Match by GUID if available
            if clash.guid:
                guid_key = clash.guid.strip().lower()
                if guid_key in by_guid:
                    candidates = by_guid[guid_key]

            # 2. Match by Name if no GUID match
            if not candidates and clash.name:
                name_key = clash.name.strip()
                if name_key in by_name:
                    candidates = by_name[name_key]
                else:
                    # Try case-insensitive name match
                    for k, v in by_name.items():
                        if k.lower() == name_key.lower():
                            candidates.extend(v)

            # 3. Evaluate candidate list
            if len(candidates) == 1:
                clash.viewpoint = candidates[0]
                matched_count += 1
            elif len(candidates) > 1:
                ambiguous_count += 1
                msg = (
                    "Ambiguous viewpoint match for clash '{0}' (guid={1}): "
                    "{2} candidate viewpoints found. Viewpoint was NOT paired automatically."
                ).format(clash.name or "", clash.guid or "", len(candidates))
                clash.diagnostics.append(msg)
                clash_payload.diagnostics.append(msg)
            else:
                # 0 candidates: safe fallback, leave viewpoint as None
                pass

    clash_payload.viewpoints = list(viewpoints_payload.viewpoints)
    clash_payload.source_format = "merged"

    diag_summary = (
        "Merged viewpoints: {0} matched, {1} ambiguous, {2} total viewpoints available."
    ).format(matched_count, ambiguous_count, len(viewpoints_payload.viewpoints))
    clash_payload.diagnostics.append(diag_summary)

    return clash_payload
