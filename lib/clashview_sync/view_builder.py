# -*- coding: utf-8 -*-
"""Safe, non-destructive 3D clash view builder for ClashView Sync.

Creates or updates 3D views in the host Revit document from a validated
ClashFramingPlan while enforcing strict safety guarantees:
  1. Never overwrites or modifies views not created by ClashView Sync.
  2. Verifies tool ownership via ExtensibleStorage schema (with mock fallback for unit tests).
  3. Never passes linked ElementIds to host view HideElements/IsolateElementsTemporary.
  4. Aborts before opening a Transaction if framing_plan is invalid or unconfirmed.
  5. Rolls back cleanly if any Revit API error occurs mid-transaction.

Compatible with Python 2.7 (pyRevit IronPython 2.7.12) and Python 3.x.
"""
from __future__ import absolute_import, division, print_function

import os
import re

from .coordinate_transformer import (
    CoordinateFrameStatus,
    FramingMethod,
    IsolationMethod,
)
from .element_resolver import (
    create_revit_element_id,
    get_element_id_value,
)

# Deterministic ExtensibleStorage Schema Identity for ClashView Sync
CLASHVIEW_SCHEMA_GUID_STR = "8E4B7C92-3F1D-4A65-9B2E-7C5D1A8F0E34"
CLASHVIEW_SCHEMA_NAME = "ClashViewSyncMetadata"
CLASHVIEW_TOOL_ID = "ClashViewSync"
CLASHVIEW_SCHEMA_VERSION = "1.0"

# Attribute name used on mock View3D objects in offline unit tests
_MOCK_METADATA_ATTR = "_clashview_sync_metadata"

# Characters prohibited in Revit view names: \ : { } [ ] | ; < > ? ` ~ and control chars
_PROHIBITED_VIEW_NAME_RE = re.compile(r'[\\:\{\}\[\]\|;\<\>\?`~\r\n\t]+')
_WHITESPACE_OR_UNDERSCORE_RE = re.compile(r'[\s_]+')

# Metadata field names stored in ExtensibleStorage
_METADATA_FIELDS = (
    "ToolId",
    "SchemaVersion",
    "Mode",
    "TestName",
    "ClashName",
    "ClashGuid",
    "SourceFile",
    "FramingMethod",
    "IsolationMethod",
    "CoordFrameStatus",
    "ParticipantAId",
    "ParticipantBId",
    "LinkInstanceAId",
    "LinkInstanceBId",
)


def _get_revit_db():
    """Attempts to import Autodesk.Revit.DB if running inside Revit / IronPython with RevitAPI."""
    try:
        import Autodesk.Revit.DB as DB
        return DB
    except Exception:
        return None


def _make_element_id_collection(int_ids):
    """Builds a strongly typed System.Collections.Generic.List[DB.ElementId] when available.

    Falls back to a standard Python list of ElementId / MockElementId objects when running
    in offline CPython unit tests without CLR/RevitAPI.
    """
    elem_ids = [create_revit_element_id(i) for i in int_ids if i is not None]
    DB = _get_revit_db()
    if DB is not None and hasattr(DB, "ElementId"):
        try:
            from System.Collections.Generic import List
            typed_list = List[DB.ElementId]()
            for eid in elem_ids:
                typed_list.Add(eid)
            return typed_list
        except Exception:
            pass
    return elem_ids


def sanitize_revit_view_name(name, max_length=180):
    """Sanitizes a string into a valid Revit view name.

    Strips all characters prohibited by Revit (\\ : { } [ ] | ; < > ? ` ~ and control
    characters), collapses consecutive whitespace/underscores into a single underscore,
    trims leading/trailing underscores, and caps total length at max_length.
    """
    if name is None:
        raw = ""
    else:
        try:
            raw = "{0}".format(name)
        except UnicodeEncodeError:
            raw = unicode(name)  # noqa: F821 (IronPython 2.7)

    # Replace prohibited characters with underscore
    cleaned = _PROHIBITED_VIEW_NAME_RE.sub("_", raw)
    # Collapse whitespace and consecutive underscores
    cleaned = _WHITESPACE_OR_UNDERSCORE_RE.sub("_", cleaned).strip("_")

    if not cleaned:
        cleaned = "Unnamed"

    if max_length and max_length > 0 and len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip("_")

    return cleaned


def format_clash_view_name(test_name, clash_name, mode="pair", max_length=180):
    """Formats a deterministic Revit 3D view name for a clash result.

    Naming convention:
      - Mode A ('pair'):    CVS_Pair_{TestName}_{ClashName}
      - Mode B ('context'): CVS_Context_{TestName}_{ClashName}

    Ensures the prefix ('CVS_Pair_' / 'CVS_Context_') and sanitized clash_name suffix
    remain intact when truncating long test names to fit within max_length.
    """
    clean_mode = str(mode).strip().lower() if mode else "pair"
    if clean_mode == "pair":
        prefix = "CVS_Pair_"
    elif clean_mode == "context":
        prefix = "CVS_Context_"
    else:
        raise ValueError(
            "Unsupported view mode '{0}': expected 'pair' or 'context'.".format(mode)
        )

    s_test = sanitize_revit_view_name(test_name or "Test", max_length=0)
    s_clash = sanitize_revit_view_name(clash_name or "Clash", max_length=0)

    max_len = int(max_length) if (max_length and int(max_length) > 24) else 180

    # Reserve room for prefix + "_" + s_clash
    # If s_clash itself is unusually long, cap s_clash to half of remaining budget
    avail_after_prefix = max_len - len(prefix)
    max_clash_len = max(16, avail_after_prefix // 2)
    if len(s_clash) > max_clash_len:
        s_clash = s_clash[:max_clash_len].rstrip("_")

    # Remaining budget for s_test (minus 1 for the separator underscore)
    max_test_len = max(4, avail_after_prefix - len(s_clash) - 1)
    if len(s_test) > max_test_len:
        s_test = s_test[:max_test_len].rstrip("_")

    full_name = "{0}{1}_{2}".format(prefix, s_test, s_clash)
    if len(full_name) > max_len:
        full_name = full_name[:max_len].rstrip("_")
    return full_name


def _get_or_create_extensible_schema():
    """Retrieves or creates the ClashViewSyncMetadata ExtensibleStorage Schema in Revit."""
    try:
        import System
        import Autodesk.Revit.DB.ExtensibleStorage as ES

        schema_guid = System.Guid(CLASHVIEW_SCHEMA_GUID_STR)
        schema = ES.Schema.Lookup(schema_guid)
        if schema is not None and schema.IsValidObject:
            return schema

        builder = ES.SchemaBuilder(schema_guid)
        builder.SetSchemaName(CLASHVIEW_SCHEMA_NAME)
        builder.SetReadAccessLevel(ES.AccessLevel.Public)
        builder.SetWriteAccessLevel(ES.AccessLevel.Public)
        for field_name in _METADATA_FIELDS:
            builder.AddSimpleField(field_name, System.String)
        return builder.Finish()
    except Exception:
        return None


def write_view_metadata(view, metadata_dict):
    """Writes ClashView Sync ownership metadata to a View3D via ExtensibleStorage or mock dict.

    Returns:
      (bool_succeeded, diagnostics_list)
    """
    diagnostics = []
    if view is None:
        return (False, ["Cannot write metadata: view is None."])

    normalized = {}
    for k in _METADATA_FIELDS:
        val = metadata_dict.get(k, "") if metadata_dict else ""
        normalized[k] = "" if val is None else "{0}".format(val)
    normalized["ToolId"] = CLASHVIEW_TOOL_ID
    normalized["SchemaVersion"] = CLASHVIEW_SCHEMA_VERSION

    # 1. Try real Revit ExtensibleStorage if view supports SetEntity and Schema is available
    schema = _get_or_create_extensible_schema()
    if schema is not None and hasattr(view, "SetEntity") and callable(view.SetEntity):
        try:
            import System
            import Autodesk.Revit.DB.ExtensibleStorage as ES

            entity = ES.Entity(schema)
            for field_name, str_val in normalized.items():
                field_obj = schema.GetField(field_name)
                if field_obj is not None:
                    try:
                        entity.Set[System.String](field_obj, System.String(str_val))
                    except Exception:
                        entity.Set(field_obj, str_val)
            view.SetEntity(entity)
            return (True, diagnostics)
        except Exception as ex:
            diagnostics.append(
                "ExtensibleStorage SetEntity failed ({0}); attempting attribute fallback.".format(
                    ex
                )
            )

    # 2. Fallback for mock View3D objects in offline unit tests
    try:
        setattr(view, _MOCK_METADATA_ATTR, dict(normalized))
        return (True, diagnostics)
    except Exception as ex:
        diagnostics.append("Failed to write view metadata: {0}".format(ex))
        return (False, diagnostics)


def read_view_metadata(view):
    """Reads ClashView Sync metadata dictionary from a View3D, or returns None if not present."""
    if view is None:
        return None

    # 1. Check mock/attribute metadata first (fast path for unit tests and in-memory mocks)
    mock_meta = getattr(view, _MOCK_METADATA_ATTR, None)
    if isinstance(mock_meta, dict) and mock_meta.get("ToolId") == CLASHVIEW_TOOL_ID:
        return dict(mock_meta)

    # 2. Check Revit ExtensibleStorage Entity if view supports GetEntity
    if hasattr(view, "GetEntity") and callable(view.GetEntity):
        schema = _get_or_create_extensible_schema()
        if schema is not None:
            try:
                import System

                entity = view.GetEntity(schema)
                if entity is not None and hasattr(entity, "IsValid") and entity.IsValid():
                    result = {}
                    for field_name in _METADATA_FIELDS:
                        field_obj = schema.GetField(field_name)
                        if field_obj is not None:
                            try:
                                val = entity.Get[System.String](field_obj)
                            except Exception:
                                val = entity.Get[str](field_name)
                            result[field_name] = "" if val is None else "{0}".format(val)
                    if result.get("ToolId") == CLASHVIEW_TOOL_ID:
                        return result
            except Exception:
                pass

    return None


def is_tool_owned_view(view):
    """Returns True ONLY if view carries verified ClashView Sync ownership metadata."""
    meta = read_view_metadata(view)
    return bool(meta and meta.get("ToolId") == CLASHVIEW_TOOL_ID)


def _collect_host_view3ds(host_doc):
    """Collects all non-template View3D instances from host_doc (live Revit or mock doc)."""
    if host_doc is None:
        return []

    # 1. Mock document support for offline unit tests
    if hasattr(host_doc, "views") and isinstance(host_doc.views, (list, tuple)):
        return [
            v
            for v in host_doc.views
            if not getattr(v, "IsTemplate", False)
        ]

    # 2. Live Revit FilteredElementCollector
    DB = _get_revit_db()
    if DB is not None and hasattr(DB, "FilteredElementCollector"):
        try:
            collector = DB.FilteredElementCollector(host_doc).OfClass(DB.View3D)
            views = []
            for v in collector:
                if not getattr(v, "IsTemplate", False):
                    views.append(v)
            return views
        except Exception:
            pass

    return []


def _find_three_dimensional_view_family_type_id(host_doc):
    """Finds the ElementId of a ThreeDimensional ViewFamilyType in host_doc."""
    if host_doc is None:
        return None

    # 1. Mock document support for offline unit tests
    if hasattr(host_doc, "view_family_type_id"):
        vft_id = getattr(host_doc, "view_family_type_id")
        if vft_id is not None:
            return create_revit_element_id(get_element_id_value(vft_id) or vft_id)
        return None

    # 2. Live Revit FilteredElementCollector
    DB = _get_revit_db()
    if DB is not None and hasattr(DB, "FilteredElementCollector"):
        try:
            collector = DB.FilteredElementCollector(host_doc).OfClass(DB.ViewFamilyType)
            for vft in collector:
                if getattr(vft, "ViewFamily", None) == DB.ViewFamily.ThreeDimensional:
                    return vft.Id
        except Exception:
            pass

    return None


def _collect_all_link_instance_ids(host_doc):
    """Returns a list of integer ElementIds for all RevitLinkInstance elements in host_doc."""
    if host_doc is None:
        return []

    # 1. Mock document support
    if hasattr(host_doc, "link_instances") and isinstance(
        host_doc.link_instances, (list, tuple)
    ):
        ids = []
        for inst in host_doc.link_instances:
            iid = get_element_id_value(getattr(inst, "Id", inst))
            if iid is not None:
                ids.append(iid)
        return ids

    # 2. Live Revit FilteredElementCollector
    DB = _get_revit_db()
    if DB is not None and hasattr(DB, "FilteredElementCollector"):
        try:
            collector = DB.FilteredElementCollector(host_doc).OfClass(
                DB.RevitLinkInstance
            )
            ids = []
            for inst in collector:
                iid = get_element_id_value(inst.Id)
                if iid is not None:
                    ids.append(iid)
            return ids
        except Exception:
            pass

    return []


def resolve_view_name_collision(host_doc, desired_name, update_existing=False):
    """Resolves a desired 3D view name against existing views in host_doc.

    Safety Rules:
      - If no view named `desired_name` exists:
          returns (desired_name, None, "CREATE_NEW", diagnostics)
      - If a view named `desired_name` exists and is NOT owned by ClashView Sync:
          NEVER overwrites or modifies it. Appends the next available numbered suffix
          (_01, _02, ...) and records a warning diagnostic.
      - If a view named `desired_name` exists and IS verified as owned by ClashView Sync:
          - If update_existing is True:
              returns (desired_name, existing_view, "REUSE", diagnostics)
          - If update_existing is False:
              appends the next available numbered suffix (_01, _02, ...) so a new view
              is created without overwriting the existing tool view.
    """
    diagnostics = []
    clean_desired = sanitize_revit_view_name(desired_name)
    existing_views = _collect_host_view3ds(host_doc)

    by_name = {}
    for v in existing_views:
        v_name = getattr(v, "Name", None)
        if v_name:
            by_name[v_name] = v

    if clean_desired not in by_name:
        return (clean_desired, None, "CREATE_NEW", diagnostics)

    colliding_view = by_name[clean_desired]
    owned = is_tool_owned_view(colliding_view)

    if owned and update_existing:
        diagnostics.append(
            "Verified existing ClashView Sync view '{0}' (ID: {1}); reusing and updating in place.".format(
                clean_desired,
                get_element_id_value(getattr(colliding_view, "Id", None)),
            )
        )
        return (clean_desired, colliding_view, "REUSE", diagnostics)

    # Compute next available numbered suffix: _01, _02, ...
    idx = 1
    while True:
        suffix = "_{0:02d}".format(idx)
        base_trimmed = clean_desired
        if len(base_trimmed) + len(suffix) > 180:
            base_trimmed = base_trimmed[: 180 - len(suffix)].rstrip("_")
        candidate_name = "{0}{1}".format(base_trimmed, suffix)
        if candidate_name not in by_name:
            break
        idx += 1

    if not owned:
        diagnostics.append(
            "View name collision guard: Existing view '{0}' is NOT owned by ClashView Sync and was preserved untouched. Using suffixed name '{1}'.".format(
                clean_desired, candidate_name
            )
        )
    else:
        diagnostics.append(
            "Existing tool-owned view '{0}' found, and update_existing=False; creating new suffixed view '{1}'.".format(
                clean_desired, candidate_name
            )
        )

    return (candidate_name, None, "CREATE_NEW", diagnostics)


class ViewCreationResult(object):
    """Structured result of create_or_update_clash_view."""

    def __init__(
        self,
        succeeded,
        action_taken,
        view=None,
        view_id=None,
        view_name="",
        mode="pair",
        framing_method=FramingMethod.UNAVAILABLE_GEOMETRY_FALLBACK,
        isolation_method=IsolationMethod.NONE,
        coord_frame_status=CoordinateFrameStatus.UNVERIFIED_COORDINATE_FRAME,
        hidden_link_ids=None,
        isolated_host_ids=None,
        metadata_written=False,
        diagnostics=None,
    ):
        self.succeeded = bool(succeeded)
        self.action_taken = action_taken
        self.view = view
        self.view_id = (
            int(view_id)
            if view_id is not None
            else (
                get_element_id_value(getattr(view, "Id", None))
                if view is not None
                else None
            )
        )
        self.view_name = view_name or (getattr(view, "Name", "") if view else "")
        self.mode = mode
        self.framing_method = framing_method
        self.isolation_method = isolation_method
        self.coord_frame_status = coord_frame_status
        self.hidden_link_ids = [int(i) for i in (hidden_link_ids or [])]
        self.isolated_host_ids = [int(i) for i in (isolated_host_ids or [])]
        self.metadata_written = bool(metadata_written)
        self.diagnostics = list(diagnostics) if diagnostics else []

    def to_dict(self):
        return {
            "succeeded": self.succeeded,
            "action_taken": self.action_taken,
            "view_id": self.view_id,
            "view_name": self.view_name,
            "mode": self.mode,
            "framing_method": self.framing_method,
            "isolation_method": self.isolation_method,
            "coord_frame_status": self.coord_frame_status,
            "hidden_link_ids": self.hidden_link_ids,
            "isolated_host_ids": self.isolated_host_ids,
            "metadata_written": self.metadata_written,
            "diagnostics": self.diagnostics,
        }

    def __repr__(self):
        return (
            "ViewCreationResult(succeeded={0}, action='{1}', view_name='{2}', "
            "view_id={3}, isolation='{4}')".format(
                self.succeeded,
                self.action_taken,
                self.view_name,
                self.view_id,
                self.isolation_method,
            )
        )


def _start_transaction(host_doc, tx_name):
    """Starts a Revit DB.Transaction or a duck-typed mock transaction on host_doc."""
    if hasattr(host_doc, "create_transaction") and callable(
        host_doc.create_transaction
    ):
        tx = host_doc.create_transaction(tx_name)
        tx.Start()
        return tx

    DB = _get_revit_db()
    if DB is not None and hasattr(DB, "Transaction"):
        tx = DB.Transaction(host_doc, tx_name)
        tx.Start()
        return tx

    raise RuntimeError(
        "Cannot start transaction: neither Revit DB.Transaction nor host_doc.create_transaction is available."
    )


def _create_isometric_view3d(host_doc, vft_id):
    """Creates a new isometric View3D in host_doc using Revit API or mock factory."""
    if hasattr(host_doc, "create_isometric_view") and callable(
        host_doc.create_isometric_view
    ):
        return host_doc.create_isometric_view(vft_id)

    DB = _get_revit_db()
    if DB is not None and hasattr(DB, "View3D"):
        return DB.View3D.CreateIsometric(host_doc, vft_id)

    raise RuntimeError("Cannot create View3D: View3D.CreateIsometric is unavailable.")


def _make_xyz(vec):
    """Constructs a Revit DB.XYZ from a Vector3, or returns Vector3 in offline mock mode."""
    DB = _get_revit_db()
    if DB is not None and hasattr(DB, "XYZ"):
        try:
            return DB.XYZ(float(vec.x), float(vec.y), float(vec.z))
        except Exception:
            pass
    return vec


def _make_section_bounding_box_xyz(section_box):
    """Constructs a Revit DB.BoundingBoxXYZ (or BoundingBox3D in mock mode) from section_box."""
    DB = _get_revit_db()
    if DB is not None and hasattr(DB, "BoundingBoxXYZ"):
        try:
            bbox_xyz = DB.BoundingBoxXYZ()
            bbox_xyz.Min = DB.XYZ(
                float(section_box.min_pt.x),
                float(section_box.min_pt.y),
                float(section_box.min_pt.z),
            )
            bbox_xyz.Max = DB.XYZ(
                float(section_box.max_pt.x),
                float(section_box.max_pt.y),
                float(section_box.max_pt.z),
            )
            return bbox_xyz
        except Exception:
            pass
    return section_box


def _find_solid_fill_pattern_id(doc):
    """Finds the ElementId of the Solid fill pattern in doc."""
    DB = _get_revit_db()
    if DB is None or doc is None:
        return None
    try:
        # 1. Search through FillPatternElement collector
        if hasattr(DB, "FilteredElementCollector") and hasattr(DB, "FillPatternElement"):
            collector = DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement)
            for fpe in collector:
                try:
                    fp = fpe.GetFillPattern()
                    if fp is not None:
                        is_solid = getattr(fp, "IsSolidFill", None)
                        if callable(is_solid):
                            is_solid = is_solid()
                        if is_solid is True:
                            return fpe.Id
                    name = getattr(fpe, "Name", "")
                    if name and name.lower() in ("<solid fill>", "solid fill", "solid"):
                        return fpe.Id
                except Exception:
                    pass

        # 2. Try static GetFillPatternElementByName
        if hasattr(DB, "FillPatternElement") and hasattr(
            DB.FillPatternElement, "GetFillPatternElementByName"
        ):
            target_types = []
            if hasattr(DB, "FillPatternTarget"):
                target_types.extend([DB.FillPatternTarget.Drafting, DB.FillPatternTarget.Model])
            for target in target_types:
                for name in ("<Solid fill>", "<Solid Fill>", "Solid fill", "Solid Fill", "Solid"):
                    try:
                        fpe = DB.FillPatternElement.GetFillPatternElementByName(doc, target, name)
                        if fpe is not None and getattr(fpe, "Id", None) is not None:
                            return fpe.Id
                    except Exception:
                        pass
    except Exception:
        pass
    return None


def _create_highlight_graphic_settings(doc, r, g, b, transparency=0):
    """Creates a Revit DB.OverrideGraphicSettings with solid vibrant fill and crisp outlines."""
    DB = _get_revit_db()
    if DB is None or not hasattr(DB, "OverrideGraphicSettings"):
        return None

    try:
        ogs = DB.OverrideGraphicSettings()
        color = DB.Color(int(r), int(g), int(b))

        solid_pattern_id = _find_solid_fill_pattern_id(doc)
        if solid_pattern_id is not None:
            # Surface Foreground Pattern: ONLY Foreground is used for Solid fill
            ogs.SetSurfaceForegroundPatternId(solid_pattern_id)
            ogs.SetSurfaceForegroundPatternColor(color)
            if hasattr(ogs, "SetSurfaceForegroundPatternVisible"):
                ogs.SetSurfaceForegroundPatternVisible(True)

            # Cut Foreground Pattern
            ogs.SetCutForegroundPatternId(solid_pattern_id)
            ogs.SetCutForegroundPatternColor(color)
            if hasattr(ogs, "SetCutForegroundPatternVisible"):
                ogs.SetCutForegroundPatternVisible(True)

        # Line Overrides (Projection and Cut): Vibrant color with prominent line weight
        ogs.SetProjectionLineColor(color)
        if hasattr(ogs, "SetProjectionLineWeight"):
            ogs.SetProjectionLineWeight(6)

        ogs.SetCutLineColor(color)
        if hasattr(ogs, "SetCutLineWeight"):
            ogs.SetCutLineWeight(6)

        # Transparency (0 = solid)
        if hasattr(ogs, "SetSurfaceTransparency"):
            ogs.SetSurfaceTransparency(int(transparency))

        # Disable Halftone
        if hasattr(ogs, "SetHalftone"):
            ogs.SetHalftone(False)

        return ogs
    except Exception:
        return None


def _highlight_element_color(view, element_id, r=0, g=200, b=0, diagnostics=None):
    """Overrides host element surface and lines with a vibrant solid color."""
    DB = _get_revit_db()
    if DB is None or not hasattr(view, "SetElementOverrides") or not hasattr(DB, "OverrideGraphicSettings"):
        return False

    try:
        doc = getattr(view, "Document", None)
        ogs = _create_highlight_graphic_settings(doc, r, g, b)
        if ogs is None:
            if diagnostics is not None:
                diagnostics.append(
                    "Warning: Could not create OverrideGraphicSettings for element {0}".format(element_id)
                )
            return False

        elem_id_obj = create_revit_element_id(element_id)
        view.SetElementOverrides(elem_id_obj, ogs)
        if diagnostics is not None:
            diagnostics.append(
                "Highlighted host element {0} in RGB({1},{2},{3})".format(element_id, r, g, b)
            )
        return True
    except Exception as ex:
        if diagnostics is not None:
            diagnostics.append(
                "Warning: SetElementOverrides failed on {0}: {1}".format(element_id, ex)
            )
        return False


def _apply_linked_category_filter(view, host_doc, category, r=220, g=30, b=30, filter_name_prefix="CVS_Clash"):
    """Applies a view filter in host_doc view targeting the category of a linked element.

    In Revit, host view filters apply to all elements in that category across all linked models!
    """
    DB = _get_revit_db()
    if DB is None or host_doc is None or view is None or category is None:
        return None

    cat_id = getattr(category, "Id", None)
    if cat_id is None:
        return None

    try:
        import System.Collections.Generic as Generic

        cat_name = getattr(category, "Name", "Category")
        safe_cat_name = re.sub(r'[^a-zA-Z0-9_]', '_', cat_name)
        filter_name = "{0}_{1}_{2}_{3}_{4}".format(filter_name_prefix, safe_cat_name, r, g, b)

        # Check if filter already exists in host_doc
        filter_elem = None
        if hasattr(DB, "FilteredElementCollector") and hasattr(DB, "ParameterFilterElement"):
            for fe in DB.FilteredElementCollector(host_doc).OfClass(DB.ParameterFilterElement):
                if getattr(fe, "Name", "") == filter_name:
                    filter_elem = fe
                    break

        if filter_elem is None and hasattr(DB, "ParameterFilterElement") and hasattr(DB.ParameterFilterElement, "Create"):
            cat_list = Generic.List[DB.ElementId]()
            cat_list.Add(cat_id)
            filter_elem = DB.ParameterFilterElement.Create(host_doc, filter_name, cat_list)

        if filter_elem is not None:
            if hasattr(view, "IsFilterApplied") and not view.IsFilterApplied(filter_elem.Id):
                view.AddFilter(filter_elem.Id)
            ogs = _create_highlight_graphic_settings(host_doc, r, g, b)
            if ogs is not None and hasattr(view, "SetFilterOverrides"):
                view.SetFilterOverrides(filter_elem.Id, ogs)
            if hasattr(view, "SetFilterVisibility"):
                view.SetFilterVisibility(filter_elem.Id, True)
            return filter_elem.Id
    except Exception:
        pass
    return None


def _apply_linked_element_filter(view, host_doc, category, element_id, r, g, b, filter_name_prefix="CVS_Clash"):
    """Highlights ONLY one specific linked element in a host view.

    Uses a view filter on the category with an Element Id (ID_PARAM) equals rule so that
    other elements of the same category are NOT affected. Returns the filter ElementId or
    None on failure. Never falls back to a category-wide filter.
    """
    try:
        DB = _get_revit_db()
        if DB is None or host_doc is None or view is None or category is None or element_id is None:
            return None

        import System.Collections.Generic as Generic

        cat_name = getattr(category, "Name", "Category")
        safe_cat_name = re.sub(r'[^a-zA-Z0-9_]', '_', cat_name)
        filter_name = "{0}_{1}_{2}_{3}_{4}_{5}".format(
            filter_name_prefix, safe_cat_name, element_id, r, g, b
        )

        filter_elem = None
        for fe in DB.FilteredElementCollector(host_doc).OfClass(DB.ParameterFilterElement):
            if getattr(fe, "Name", "") == filter_name:
                filter_elem = fe
                break

        if filter_elem is None:
            cat_list = Generic.List[DB.ElementId]()
            cat_list.Add(category.Id)
            rule = DB.ParameterFilterRuleFactory.CreateEqualsRule(
                DB.ElementId(DB.BuiltInParameter.ID_PARAM),
                create_revit_element_id(element_id),
            )
            elem_filter = DB.ElementParameterFilter(rule)
            filter_elem = DB.ParameterFilterElement.Create(host_doc, filter_name, cat_list, elem_filter)

        if not view.IsFilterApplied(filter_elem.Id):
            view.AddFilter(filter_elem.Id)
        ogs = _create_highlight_graphic_settings(host_doc, r, g, b)
        if ogs is not None:
            view.SetFilterOverrides(filter_elem.Id, ogs)
        view.SetFilterVisibility(filter_elem.Id, True)
        return filter_elem.Id
    except Exception:
        return None


_HIGHLIGHT_APP_ID = "ClashViewSync"


def _remove_highlight_directshapes(host_doc):
    """Deletes DirectShape highlight proxies created by previous runs."""
    DB = _get_revit_db()
    removed = 0
    try:
        if DB is None or host_doc is None or not hasattr(DB, "DirectShape"):
            return 0
        stale = []
        for ds in DB.FilteredElementCollector(host_doc).OfClass(DB.DirectShape):
            if getattr(ds, "ApplicationId", "") == _HIGHLIGHT_APP_ID:
                stale.append(ds.Id)
        for sid in stale:
            try:
                host_doc.Delete(sid)
                removed += 1
            except Exception:
                pass
    except Exception:
        pass
    return removed


def _collect_element_solids(elem):
    """Returns the visible Solids (Fine detail) of an element, in its own document coordinates."""
    DB = _get_revit_db()
    solids = []
    opts = DB.Options()
    opts.DetailLevel = DB.ViewDetailLevel.Fine
    opts.ComputeReferences = False
    opts.IncludeNonVisibleObjects = False
    geo = elem.get_Geometry(opts)
    if geo is None:
        return solids

    def walk(geometry):
        for obj in geometry:
            if isinstance(obj, DB.Solid):
                if obj.Volume > 1e-9 and obj.Faces.Size > 0:
                    solids.append(obj)
            elif isinstance(obj, DB.GeometryInstance):
                walk(obj.GetInstanceGeometry())

    walk(geo)
    return solids


def _highlight_linked_via_directshape(view, host_doc, res, r, g, b, label, diagnostics=None, proxy_ids=None):
    """Highlights ONE linked element: copies its solids (transformed by the link transform)
    into a tagged DirectShape in the host and colors that DirectShape in this view only.

    Revit offers no per-element graphic override for linked elements, so this proxy is the
    reliable way to color exactly the clashing element. Proxies are removed on every run.
    """
    DB = _get_revit_db()
    try:
        elem = getattr(res, "linked_element", None) or getattr(res, "element", None)
        inst = getattr(res, "link_instance", None)
        if DB is None or elem is None or inst is None or not hasattr(DB, "DirectShape"):
            return False

        xf = inst.GetTotalTransform()
        solids = _collect_element_solids(elem)
        if not solids:
            if diagnostics is not None:
                diagnostics.append("Warning: linked {0} has no solid geometry to highlight.".format(label))
            return False

        import System.Collections.Generic as Generic

        shapes = Generic.List[DB.GeometryObject]()
        for s in solids:
            placed = None
            try:
                # Tiny uniform grow about the centroid so the proxy does not z-fight the real element
                c = xf.OfPoint(s.ComputeCentroid())
                k = 1.004
                grow = DB.Transform.Identity
                grow.BasisX = DB.XYZ(k, 0, 0)
                grow.BasisY = DB.XYZ(0, k, 0)
                grow.BasisZ = DB.XYZ(0, 0, k)
                grow.Origin = DB.XYZ(c.X * (1 - k), c.Y * (1 - k), c.Z * (1 - k))
                placed = DB.SolidUtils.CreateTransformed(s, grow.Multiply(xf))
            except Exception:
                placed = None
            if placed is None:
                placed = DB.SolidUtils.CreateTransformed(s, xf)
            shapes.Add(placed)

        ds = DB.DirectShape.CreateElement(host_doc, DB.ElementId(DB.BuiltInCategory.OST_GenericModel))
        ds.ApplicationId = _HIGHLIGHT_APP_ID
        ds.ApplicationDataId = "highlight_{0}".format(label)
        try:
            ds.Name = "CVS Highlight {0}".format(label)
        except Exception:
            pass
        ds.SetShape(shapes)
        if proxy_ids is not None:
            try:
                proxy_ids.append(int(get_element_id_value(ds.Id)))
            except Exception:
                pass
        ogs = _create_highlight_graphic_settings(host_doc, r, g, b)
        if ogs is not None:
            view.SetElementOverrides(ds.Id, ogs)
        try:
            # Isolated mode hides non-participating model categories; keep the proxy's category visible
            gm_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)
            if view.GetCategoryHidden(gm_id):
                view.SetCategoryHidden(gm_id, False)
        except Exception:
            pass
        if diagnostics is not None:
            diagnostics.append(
                "Highlighted linked {0} via color proxy ({1} solid(s)) in RGB({2},{3},{4})".format(
                    label, len(solids), r, g, b
                )
            )
        return True
    except Exception as ex:
        if diagnostics is not None:
            diagnostics.append("Warning: color proxy failed for linked {0}: {1}".format(label, ex))
        return False


def _ensure_proxies_visible(view, proxy_ids):
    """Safety net after isolation: unhide the color proxies and keep Generic Models visible."""
    DB = _get_revit_db()
    try:
        if proxy_ids and hasattr(view, "UnhideElements"):
            try:
                view.UnhideElements(_make_element_id_collection(list(proxy_ids)))
            except Exception:
                pass
        if DB is not None and proxy_ids:
            gm_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)
            if view.GetCategoryHidden(gm_id):
                view.SetCategoryHidden(gm_id, False)
    except Exception:
        pass


def _remove_clash_highlight_filters(view, host_doc):
    """Removes every 'CVS_Clash*' view filter from the view (stale category-wide or element
    filters from previous runs) and deletes old highlight DirectShape proxies.
    Returns the number of filters removed."""
    removed = 0
    _remove_highlight_directshapes(host_doc)
    try:
        for fid in list(view.GetFilters()):
            try:
                fe = host_doc.GetElement(fid)
                if fe is not None and getattr(fe, "Name", "").startswith("CVS_Clash"):
                    view.RemoveFilter(fid)
                    removed += 1
            except Exception:
                pass
    except Exception:
        pass
    return removed


def _highlight_participant(view, host_doc, res, r, g, b, label="Participant", diagnostics=None, proxy_ids=None):
    """Applies vibrant color highlighting to a clash participant, supporting both Host and Linked elements."""
    if res is None or not getattr(res, "is_resolved", False):
        return False

    DB = _get_revit_db()
    is_linked = getattr(res, "is_linked", False)
    eid = getattr(res, "resolved_element_id", None)

    if not is_linked:
        # Host element: direct view element override
        if eid is not None:
            return _highlight_element_color(view, eid, r=r, g=g, b=b, diagnostics=diagnostics)
    else:
        # Linked element: preferred = DirectShape color proxy (exactly this element)
        if _highlight_linked_via_directshape(view, host_doc, res, r, g, b, label, diagnostics, proxy_ids):
            return True
        # Secondary: Element-Id view filter (only this element)
        elem = getattr(res, "linked_element", None) or getattr(res, "element", None)
        cat = getattr(elem, "Category", None) if elem is not None else None
        if cat is not None and getattr(cat, "Id", None) is not None and eid is not None:
            filter_id = _apply_linked_element_filter(
                view, host_doc, cat, eid, r, g, b, filter_name_prefix="CVS_Clash_{0}".format(label)
            )
            if filter_id is not None:
                if diagnostics is not None:
                    diagnostics.append(
                        "Highlighted linked {0} (element {1}, Category '{2}') via Element-Id view filter in RGB({3},{4},{5})".format(
                            label, eid, getattr(cat, "Name", ""), r, g, b
                        )
                    )
                return True
            if diagnostics is not None:
                diagnostics.append(
                    "Warning: could not highlight linked {0} (element {1}) individually; skipped to avoid coloring the whole category.".format(
                        label, eid
                    )
                )
    return False


def _make_view_orientation_3d(eye_pos, up_vec, forward_vec):
    """Constructs a Revit DB.ViewOrientation3D(eyePosition, upDirection, forwardDirection)."""
    DB = _get_revit_db()
    if DB is not None and hasattr(DB, "ViewOrientation3D"):
        try:
            return DB.ViewOrientation3D(
                _make_xyz(eye_pos),
                _make_xyz(up_vec),
                _make_xyz(forward_vec),
            )
        except Exception:
            pass
    return {
        "eye_position": eye_pos,
        "up_direction": up_vec,
        "forward_direction": forward_vec,
    }


# Distracting annotation and datum categories hidden by default in 3D clash views
CLUTTER_BUILTIN_CATEGORIES = (
    "OST_VolumeOfInterest",  # Scope Boxes (green wireframe clutter)
    "OST_Levels",            # Levels
    "OST_Grids",             # Grids
    "OST_CLines",            # Reference Planes
)


def hide_clutter_categories(view, diagnostics=None):
    """Hides distracting annotation and datum categories (Scope Boxes, Levels, Grids, Ref Planes) in view.

    Returns list of category names that were successfully hidden.
    """
    hidden = []
    DB = _get_revit_db()

    # 1. Live Revit API
    if DB is not None and hasattr(DB, "BuiltInCategory"):
        for cat_name in CLUTTER_BUILTIN_CATEGORIES:
            try:
                if hasattr(DB.BuiltInCategory, cat_name):
                    bic = getattr(DB.BuiltInCategory, cat_name)
                    cat_id = DB.ElementId(bic)
                    if hasattr(view, "CanCategoryBeHidden") and view.CanCategoryBeHidden(cat_id):
                        view.SetCategoryHidden(cat_id, True)
                        hidden.append(cat_name)
            except Exception as ex:
                if diagnostics is not None:
                    diagnostics.append(
                        "Notice: Could not hide category {0}: {1}".format(cat_name, ex)
                    )

    # 2. Mock View3D support for unit tests
    if hasattr(view, "SetCategoryHidden") and (DB is None or not hasattr(DB, "BuiltInCategory")):
        for cat_name in CLUTTER_BUILTIN_CATEGORIES:
            try:
                if hasattr(view, "CanCategoryBeHidden") and view.CanCategoryBeHidden(cat_name):
                    view.SetCategoryHidden(cat_name, True)
                    hidden.append(cat_name)
            except Exception:
                pass

    return hidden


def sync_link_visibility_in_vv(
    view, host_doc, res_a, res_b, mode="pair", diagnostics=None
):
    """Controls Revit Link visibility checkboxes in the Visibility/Graphics Overrides (VV) dialog.

    In Mode A ('pair' / Isolated 3D Crop Box):
      - In VV -> Revit Links tab: ONLY links affiliated with the clash are switched ON (visible).
      - All unaffiliated Revit link categories/subcategories are switched OFF (hidden).
      - The main BuiltInCategory.OST_RvtLinks category remains enabled if any affiliated link exists.

    Returns a tuple of (switched_on_names, switched_off_names).
    """
    if mode != "pair" or view is None or host_doc is None:
        return ([], [])

    diags = diagnostics if diagnostics is not None else []
    DB = _get_revit_db()

    # 1. Identify affiliated links (instances, types, categories, model names)
    affiliated_inst_ids = set()
    affiliated_type_ids = set()
    affiliated_cat_ids = set()
    affiliated_names = set()

    for r in (res_a, res_b):
        if (
            r is not None
            and getattr(r, "is_resolved", False)
            and getattr(r, "is_linked", False)
        ):
            lid = getattr(r, "link_instance_id", None)
            if lid is not None:
                lid_int = get_element_id_value(lid)
                if lid_int is not None:
                    affiliated_inst_ids.add(lid_int)

            m_name = getattr(r, "model_name", None)
            if m_name:
                cleaned = os.path.splitext(os.path.basename(m_name))[0].lower().strip()
                affiliated_names.add(cleaned)
                affiliated_names.add(m_name.lower().strip())

            inst = getattr(r, "link_instance", None)
            if inst is None and lid is not None and hasattr(host_doc, "GetElement"):
                try:
                    inst = host_doc.GetElement(create_revit_element_id(lid))
                except Exception:
                    inst = None

            if inst is not None:
                inst_cat = getattr(inst, "Category", None)
                if inst_cat is not None and getattr(inst_cat, "Id", None) is not None:
                    cid = get_element_id_value(inst_cat.Id)
                    if cid is not None:
                        affiliated_cat_ids.add(cid)

                inst_name = getattr(inst, "Name", None)
                if inst_name:
                    cleaned_i = os.path.splitext(os.path.basename(inst_name))[0].lower().strip()
                    affiliated_names.add(cleaned_i)
                    affiliated_names.add(inst_name.lower().strip())

                if hasattr(inst, "GetTypeId"):
                    try:
                        tid = inst.GetTypeId()
                        val_tid = get_element_id_value(tid)
                        if val_tid is not None:
                            affiliated_type_ids.add(val_tid)
                        t_elem = (
                            host_doc.GetElement(tid)
                            if hasattr(host_doc, "GetElement")
                            else None
                        )
                        if t_elem is not None:
                            t_cat = getattr(t_elem, "Category", None)
                            if t_cat is not None and getattr(t_cat, "Id", None) is not None:
                                val_c = get_element_id_value(t_cat.Id)
                                if val_c is not None:
                                    affiliated_cat_ids.add(val_c)
                            t_name = getattr(t_elem, "Name", None)
                            if t_name:
                                cleaned_t = os.path.splitext(os.path.basename(t_name))[0].lower().strip()
                                affiliated_names.add(cleaned_t)
                                affiliated_names.add(t_name.lower().strip())
                    except Exception:
                        pass

    # 2. Collect all link categories / subcategories in host_doc
    candidate_categories = []  # list of (cat_id, cat_name)
    seen_cat_ids = set()

    # A) From OST_RvtLinks SubCategories in host_doc.Settings.Categories
    try:
        settings = getattr(host_doc, "Settings", None)
        cats = getattr(settings, "Categories", None) if settings is not None else None
        if cats is not None:
            bic_id = (
                DB.BuiltInCategory.OST_RvtLinks
                if (DB is not None and hasattr(DB, "BuiltInCategory"))
                else -2001352
            )
            rvt_links_cat = cats.get_Item(bic_id) if hasattr(cats, "get_Item") else None
            if rvt_links_cat is not None:
                subcats = getattr(rvt_links_cat, "SubCategories", None)
                if subcats is not None:
                    for sc in subcats:
                        sc_id = get_element_id_value(getattr(sc, "Id", None))
                        if sc_id is not None and sc_id not in seen_cat_ids:
                            seen_cat_ids.add(sc_id)
                            candidate_categories.append((sc_id, getattr(sc, "Name", "")))
    except Exception:
        pass

    # B) From DB.FilteredElementCollector(host_doc).OfClass(DB.RevitLinkType)
    if (
        DB is not None
        and hasattr(DB, "FilteredElementCollector")
        and hasattr(DB, "RevitLinkType")
    ):
        try:
            collector = DB.FilteredElementCollector(host_doc).OfClass(DB.RevitLinkType)
            for lt in collector:
                lt_cat = getattr(lt, "Category", None)
                if lt_cat is not None:
                    c_id = get_element_id_value(getattr(lt_cat, "Id", None))
                    if c_id is not None and c_id not in seen_cat_ids:
                        seen_cat_ids.add(c_id)
                        candidate_categories.append((c_id, getattr(lt, "Name", "")))
        except Exception:
            pass

    # C) From DB.FilteredElementCollector(host_doc).OfClass(DB.RevitLinkInstance)
    if (
        DB is not None
        and hasattr(DB, "FilteredElementCollector")
        and hasattr(DB, "RevitLinkInstance")
    ):
        try:
            collector = DB.FilteredElementCollector(host_doc).OfClass(
                DB.RevitLinkInstance
            )
            for li in collector:
                li_cat = getattr(li, "Category", None)
                if li_cat is not None:
                    c_id = get_element_id_value(getattr(li_cat, "Id", None))
                    if c_id is not None and c_id not in seen_cat_ids:
                        seen_cat_ids.add(c_id)
                        candidate_categories.append((c_id, getattr(li, "Name", "")))
        except Exception:
            pass

    # D) From mock / test host_doc
    for lt in getattr(host_doc, "link_types", []):
        lt_cat = getattr(lt, "Category", None)
        c_id = get_element_id_value(getattr(lt_cat, "Id", None)) if lt_cat else None
        if c_id is not None and c_id not in seen_cat_ids:
            seen_cat_ids.add(c_id)
            candidate_categories.append((c_id, getattr(lt, "Name", "")))

    for inst in getattr(host_doc, "link_instances", []):
        inst_cat = getattr(inst, "Category", None)
        c_id = get_element_id_value(getattr(inst_cat, "Id", None)) if inst_cat else None
        if c_id is not None and c_id not in seen_cat_ids:
            seen_cat_ids.add(c_id)
            candidate_categories.append((c_id, getattr(inst, "Name", "")))

    # Ensure parent OST_RvtLinks category is enabled if we have affiliated links
    if DB is not None and hasattr(DB, "BuiltInCategory") and hasattr(DB, "ElementId"):
        try:
            parent_cat_id = DB.ElementId(DB.BuiltInCategory.OST_RvtLinks)
            if hasattr(view, "CanCategoryBeHidden") and view.CanCategoryBeHidden(
                parent_cat_id
            ):
                view.SetCategoryHidden(parent_cat_id, False)
        except Exception:
            pass

    # 3. Apply Visibility to each link category in VV
    switched_on = []
    switched_off = []

    for c_id, c_name in candidate_categories:
        revit_cid = create_revit_element_id(c_id)
        if hasattr(view, "CanCategoryBeHidden") and not view.CanCategoryBeHidden(
            revit_cid
        ):
            continue

        # Check affiliation:
        is_affiliated = False
        if c_id in affiliated_cat_ids:
            is_affiliated = True
        elif c_name:
            c_clean = os.path.splitext(os.path.basename(c_name))[0].lower().strip()
            for aff_name in affiliated_names:
                if aff_name and (aff_name in c_clean or c_clean in aff_name):
                    is_affiliated = True
                    break

        try:
            if is_affiliated:
                view.SetCategoryHidden(revit_cid, False)
                switched_on.append(c_name or str(c_id))
            else:
                view.SetCategoryHidden(revit_cid, True)
                switched_off.append(c_name or str(c_id))
        except Exception as ex:
            diags.append(
                "Failed to set VV visibility for link category {0}: {1}".format(
                    c_id, ex
                )
            )

    if switched_on or switched_off:
        diags.append(
            "VV Revit Links synced: {0} link(s) switched ON ({1}), {2} switched OFF in Visibility/Graphics.".format(
                len(switched_on),
                ", ".join(switched_on) if switched_on else "None",
                len(switched_off),
            )
        )

    return (switched_on, switched_off)


def isolate_clash_participant_categories(
    view, host_doc, res_a, res_b, mode="pair", diagnostics=None
):
    """In Mode A ('pair' / Isolated 3D Crop Box):
    Hides all Model categories in view EXCEPT the categories of the two clashing participants
    (and BuiltInCategory.OST_RvtLinks if a link is involved).

    This ensures that ONLY the 2 clashing elements are visible in the isolated view!
    """
    if mode != "pair" or view is None or host_doc is None:
        return []

    diags = diagnostics if diagnostics is not None else []
    DB = _get_revit_db()

    # 1. Identify Category IDs that must remain VISIBLE
    keep_cat_ids = set()
    keep_cat_names = set()

    for r in (res_a, res_b):
        if r is not None and getattr(r, "is_resolved", False):
            elem = getattr(r, "element", None)
            if elem is None:
                elem = getattr(r, "host_element", None) or getattr(r, "linked_element", None)
            if elem is not None:
                cat = getattr(elem, "Category", None)
                if cat is not None:
                    cid = get_element_id_value(getattr(cat, "Id", None))
                    if cid is not None:
                        keep_cat_ids.add(cid)
                    cname = getattr(cat, "Name", None)
                    if cname:
                        keep_cat_names.add(cname.lower().strip())

    # Always keep OST_RvtLinks visible if any participant is linked
    if any(r is not None and getattr(r, "is_linked", False) for r in (res_a, res_b)):
        if DB is not None and hasattr(DB, "BuiltInCategory"):
            rvt_links_id = int(DB.BuiltInCategory.OST_RvtLinks)
            keep_cat_ids.add(rvt_links_id)
        else:
            keep_cat_ids.add(-2001352)

    hidden_count = 0
    # 2. Iterate through host_doc.Settings.Categories and hide all other Model categories
    try:
        settings = getattr(host_doc, "Settings", None)
        categories = getattr(settings, "Categories", None) if settings is not None else None
        if categories is None and hasattr(host_doc, "categories"):
            categories = host_doc.categories

        if categories is not None:
            for cat in categories:
                cat_type = getattr(cat, "CategoryType", None)
                is_model = True
                if cat_type is not None and DB is not None and hasattr(DB, "CategoryType"):
                    is_model = (cat_type == DB.CategoryType.Model)

                if not is_model:
                    continue

                cat_id = getattr(cat, "Id", None)
                cid_val = get_element_id_value(cat_id)
                if cid_val is None:
                    continue

                if cid_val in keep_cat_ids:
                    if hasattr(view, "CanCategoryBeHidden") and view.CanCategoryBeHidden(cat_id):
                        view.SetCategoryHidden(cat_id, False)
                else:
                    if hasattr(view, "CanCategoryBeHidden") and view.CanCategoryBeHidden(cat_id):
                        view.SetCategoryHidden(cat_id, True)
                        hidden_count += 1
    except Exception as ex:
        diags.append("Notice: Model category isolation encountered an issue: {0}".format(ex))

    if hidden_count > 0:
        diags.append(
            "Category isolation active: Kept participant categories visible and hid {0} other Model categories in view.".format(
                hidden_count
            )
        )

    return diags


def _norm_str(text):
    """Normalized alphanumeric lowercase string for robust comparisons."""
    if text is None:
        return ""
    return re.sub(r'[\s_\-]+', '', str(text)).lower()


def _find_existing_clash_view(host_doc, test_name, clash_name, clash_guid=None):
    """Finds any existing tool-owned 3D view for this clash across any mode or prefix.

    Returns the matching View3D element or None.
    """
    if host_doc is None:
        return None

    existing_views = _collect_host_view3ds(host_doc)
    if not existing_views:
        return None

    clean_test = _norm_str(test_name)
    clean_clash = _norm_str(clash_name)
    clean_guid = str(clash_guid).strip().lower() if clash_guid else ""

    # 1. Primary search: Match tool-owned views by ExtensibleStorage / mock metadata
    for v in existing_views:
        if not is_tool_owned_view(v):
            continue
        meta = read_view_metadata(v)
        if not meta:
            continue

        m_guid = str(meta.get("ClashGuid") or "").strip().lower()
        if clean_guid and m_guid and clean_guid == m_guid:
            return v

        m_clash = _norm_str(meta.get("ClashName"))
        m_test = _norm_str(meta.get("TestName"))
        if clean_clash and m_clash == clean_clash:
            if not clean_test or not m_test or clean_test == m_test:
                return v

    # 2. Secondary search: Match tool-owned views by deterministic naming patterns
    for v in existing_views:
        if not is_tool_owned_view(v):
            continue
        v_name = getattr(v, "Name", "")
        norm_vname = _norm_str(v_name)
        if clean_clash and clean_clash in norm_vname:
            if not clean_test or clean_test in norm_vname:
                return v

    return None


def restore_context_visibility(view, host_doc, all_link_ids=None, diagnostics=None):
    """Restores surrounding model, link, and category visibility on a View3D for context mode.

    Reverses category isolation, unhides all link subcategories in VV, unhides all link
    instances, disables temporary hide/isolate, and unhides any hidden elements within the
    section box.
    """
    if view is None or host_doc is None:
        return

    diags = diagnostics if diagnostics is not None else []
    DB = _get_revit_db()

    # 1. Reset Temporary Hide/Isolate mode if active
    if hasattr(view, "DisableTemporaryViewMode") and DB is not None and hasattr(DB, "TemporaryViewMode"):
        try:
            view.DisableTemporaryViewMode(DB.TemporaryViewMode.TemporaryHideIsolate)
        except Exception:
            pass

    # 2. Unhide all Model categories (reversing isolate_clash_participant_categories)
    try:
        settings = getattr(host_doc, "Settings", None)
        categories = getattr(settings, "Categories", None) if settings is not None else None
        if categories is None and hasattr(host_doc, "categories"):
            categories = host_doc.categories

        if categories is not None:
            for cat in categories:
                cat_type = getattr(cat, "CategoryType", None)
                is_model = True
                if cat_type is not None and DB is not None and hasattr(DB, "CategoryType"):
                    is_model = (cat_type == DB.CategoryType.Model)
                if not is_model:
                    continue
                cat_id = getattr(cat, "Id", None)
                if cat_id is not None and hasattr(view, "CanCategoryBeHidden") and view.CanCategoryBeHidden(cat_id):
                    try:
                        view.SetCategoryHidden(cat_id, False)
                    except Exception:
                        pass
    except Exception as ex:
        diags.append("Notice restoring model categories: {0}".format(ex))

    # 3. Unhide all Revit Link categories and subcategories in VV (reversing sync_link_visibility_in_vv)
    if DB is not None and hasattr(DB, "BuiltInCategory") and hasattr(DB, "ElementId"):
        try:
            rvt_links_id = DB.ElementId(DB.BuiltInCategory.OST_RvtLinks)
            if hasattr(view, "CanCategoryBeHidden") and view.CanCategoryBeHidden(rvt_links_id):
                view.SetCategoryHidden(rvt_links_id, False)
        except Exception:
            pass

    try:
        if categories is not None:
            bic_id = (
                DB.BuiltInCategory.OST_RvtLinks
                if (DB is not None and hasattr(DB, "BuiltInCategory"))
                else -2001352
            )
            rvt_links_cat = categories.get_Item(bic_id) if hasattr(categories, "get_Item") else None
            if rvt_links_cat is not None:
                subcats = getattr(rvt_links_cat, "SubCategories", None)
                if subcats is not None:
                    for sc in subcats:
                        sc_id = getattr(sc, "Id", None)
                        if sc_id is not None and hasattr(view, "CanCategoryBeHidden") and view.CanCategoryBeHidden(sc_id):
                            try:
                                view.SetCategoryHidden(sc_id, False)
                            except Exception:
                                pass
    except Exception:
        pass

    if DB is not None and hasattr(DB, "FilteredElementCollector") and hasattr(DB, "RevitLinkType"):
        try:
            for lt in DB.FilteredElementCollector(host_doc).OfClass(DB.RevitLinkType):
                lt_cat = getattr(lt, "Category", None)
                if lt_cat is not None:
                    c_id = getattr(lt_cat, "Id", None)
                    if c_id is not None and hasattr(view, "CanCategoryBeHidden") and view.CanCategoryBeHidden(c_id):
                        try:
                            view.SetCategoryHidden(c_id, False)
                        except Exception:
                            pass
        except Exception:
            pass

    # 4. Unhide all RevitLinkInstance elements in the view
    if all_link_ids is None:
        all_link_ids = _collect_all_link_instance_ids(host_doc)
    if all_link_ids and hasattr(view, "UnhideElements"):
        try:
            view.UnhideElements(_make_element_id_collection(all_link_ids))
        except Exception:
            pass

    # 4b. Reset any custom link overrides (e.g. individually hidden linked elements) in Revit 2024
    if hasattr(view, "RemoveLinkOverrides"):
        for lid in (all_link_ids or []):
            try:
                view.RemoveLinkOverrides(create_revit_element_id(lid))
            except Exception:
                pass

    # 5. Unhide any host elements within the section box that were hidden
    if DB is not None and hasattr(DB, "FilteredElementCollector") and hasattr(view, "UnhideElements"):
        try:
            sbox = getattr(view, "GetSectionBox", None)
            if callable(sbox):
                sbox_geom = sbox()
                if sbox_geom is not None and hasattr(DB, "Outline") and hasattr(DB, "BoundingBoxIntersectsFilter"):
                    outline = DB.Outline(sbox_geom.Min, sbox_geom.Max)
                    filter_bbox = DB.BoundingBoxIntersectsFilter(outline)
                    collector = DB.FilteredElementCollector(host_doc).WherePasses(filter_bbox)
                    hidden_ids = []
                    for elem in collector:
                        try:
                            if hasattr(elem, "IsHidden") and elem.IsHidden(view):
                                hidden_ids.append(elem.Id)
                        except Exception:
                            pass
                    if hidden_ids:
                        view.UnhideElements(_make_element_id_collection([get_element_id_value(i) for i in hidden_ids]))
        except Exception:
            pass

    # 6. Mock View3D support for unit tests: preserve clutter categories hidden
    if hasattr(view, "hidden_category_ids") and isinstance(view.hidden_category_ids, list):
        view.hidden_category_ids = [
            cid for cid in view.hidden_category_ids
            if cid in CLUTTER_BUILTIN_CATEGORIES
        ]
    if hasattr(view, "hidden_element_ids"):
        view.hidden_element_ids = []


def create_or_update_clash_view(
    host_doc,
    framing_plan,
    test_name,
    clash_name,
    res_a=None,
    res_b=None,
    clash_guid=None,
    source_file=None,
    update_existing=False,
    allow_single_participant_fallback=True,
    highlight_elements=True,
):
    """Creates or updates a 3D clash view in host_doc from a validated ClashFramingPlan.

    Enforces pre-transaction guardrails, view ownership checks, honest visibility/isolation
    handling for host vs. linked elements, and clean transaction rollback on error.
    """
    diagnostics = []

    if host_doc is None:
        return ViewCreationResult(
            succeeded=False,
            action_taken="ABORTED",
            diagnostics=["Cannot create clash view: host_doc is None."],
        )

    if framing_plan is None:
        return ViewCreationResult(
            succeeded=False,
            action_taken="ABORTED",
            diagnostics=["Cannot create clash view: framing_plan is None."],
        )

    mode = getattr(framing_plan, "mode", "pair") or "pair"
    framing_method = getattr(
        framing_plan, "framing_method", FramingMethod.UNAVAILABLE_GEOMETRY_FALLBACK
    )
    isolation_method = getattr(
        framing_plan, "isolation_method", IsolationMethod.NONE
    )
    coord_frame_status = getattr(
        framing_plan,
        "coord_frame_status",
        CoordinateFrameStatus.UNVERIFIED_COORDINATE_FRAME,
    )
    if getattr(framing_plan, "diagnostics", None):
        diagnostics.extend(framing_plan.diagnostics)

    # Guardrail 1: Valid framing plan and section box required before opening any Transaction
    if not getattr(framing_plan, "is_valid", False) or getattr(
        framing_plan, "section_box", None
    ) is None:
        diagnostics.append(
            "Aborted before Transaction: framing_plan is invalid or has no section_box (framing_method={0}).".format(
                framing_method
            )
        )
        return ViewCreationResult(
            succeeded=False,
            action_taken="ABORTED",
            mode=mode,
            framing_method=framing_method,
            isolation_method=isolation_method,
            coord_frame_status=coord_frame_status,
            diagnostics=diagnostics,
        )

    # Guardrail 2: Single-participant fallback requires explicit user opt-in
    if (
        framing_method == FramingMethod.SINGLE_PARTICIPANT_FALLBACK
        and not allow_single_participant_fallback
    ):
        diagnostics.append(
            "Aborted before Transaction: framing_plan uses SINGLE_PARTICIPANT_FALLBACK and allow_single_participant_fallback is False."
        )
        return ViewCreationResult(
            succeeded=False,
            action_taken="ABORTED",
            mode=mode,
            framing_method=framing_method,
            isolation_method=isolation_method,
            coord_frame_status=coord_frame_status,
            diagnostics=diagnostics,
        )

    desired_name = format_clash_view_name(test_name, clash_name, mode=mode)

    # Check if an existing view for this clash already exists (in ANY mode) when update_existing=True
    existing_tool_view = None
    if update_existing:
        existing_tool_view = _find_existing_clash_view(
            host_doc, test_name, clash_name, clash_guid=clash_guid
        )

    if existing_tool_view is not None:
        resolved_name = desired_name
        existing_view = existing_tool_view
        collision_action = "REUSE"
        diagnostics.append(
            "Found existing tool view '{0}' for {1}/{2}; updating in place with mode '{3}'.".format(
                getattr(existing_view, "Name", ""), test_name, clash_name, mode
            )
        )
    else:
        resolved_name, existing_view, collision_action, collision_diags = (
            resolve_view_name_collision(
                host_doc, desired_name, update_existing=update_existing
            )
        )
        diagnostics.extend(collision_diags)

    # Guardrail 3: If creating a new view, verify a 3D ViewFamilyType exists before Transaction
    vft_id = None
    if collision_action == "CREATE_NEW":
        vft_id = _find_three_dimensional_view_family_type_id(host_doc)
        if vft_id is None:
            diagnostics.append(
                "Aborted before Transaction: No ThreeDimensional ViewFamilyType found in host document."
            )
            return ViewCreationResult(
                succeeded=False,
                action_taken="ABORTED",
                view_name=resolved_name,
                mode=mode,
                framing_method=framing_method,
                isolation_method=isolation_method,
                coord_frame_status=coord_frame_status,
                diagnostics=diagnostics,
            )

    tx_title = "ClashView Sync: {0}".format(resolved_name)
    tx = None
    hidden_link_ids = []
    isolated_host_ids = []

    try:
        tx = _start_transaction(host_doc, tx_title)

        if collision_action == "REUSE" and existing_view is not None:
            view = existing_view
            action_taken = "UPDATED"
            # Unlock view orientation if locked before modifying camera/section box
            if getattr(view, "IsLocked", False) and hasattr(view, "Unlock"):
                try:
                    view.Unlock()
                except Exception as ex:
                    diagnostics.append("Could not unlock existing 3D view: {0}".format(ex))

            # Reset temporary hide/isolate mode if active
            DB = _get_revit_db()
            if hasattr(view, "DisableTemporaryViewMode") and DB is not None:
                try:
                    view.DisableTemporaryViewMode(
                        DB.TemporaryViewMode.TemporaryHideIsolate
                    )
                except Exception:
                    pass
        else:
            view = _create_isometric_view3d(host_doc, vft_id)
            action_taken = "CREATED"

        # Detach restrictive view template if present
        DB = _get_revit_db()
        try:
            if DB is not None and hasattr(DB.ElementId, "InvalidElementId"):
                view.ViewTemplateId = DB.ElementId.InvalidElementId
            else:
                view.ViewTemplateId = create_revit_element_id(-1)
        except Exception as ex:
            diagnostics.append("Notice: Could not clear ViewTemplateId: {0}".format(ex))

        # Configure DetailLevel (Fine) and DisplayStyle (ShadingWithEdges)
        try:
            if DB is not None and hasattr(DB, "ViewDetailLevel"):
                view.DetailLevel = DB.ViewDetailLevel.Fine
            else:
                view.DetailLevel = "Fine"
        except Exception as ex:
            diagnostics.append("Notice: Could not set DetailLevel to Fine: {0}".format(ex))

        try:
            if DB is not None and hasattr(DB, "DisplayStyle"):
                view.DisplayStyle = DB.DisplayStyle.ShadingWithEdges
            else:
                view.DisplayStyle = "ShadingWithEdges"
            # Explicitly enforce Shading With Edges via BuiltInParameter MODEL_GRAPHICS_STYLE (3)
            if DB is not None and hasattr(DB, "BuiltInParameter") and hasattr(view, "get_Parameter"):
                mg_param = view.get_Parameter(DB.BuiltInParameter.MODEL_GRAPHICS_STYLE)
                if mg_param is not None and not mg_param.IsReadOnly:
                    mg_param.Set(3)
        except Exception as ex:
            diagnostics.append(
                "Notice: Could not set DisplayStyle to ShadingWithEdges: {0}".format(ex)
            )

        # Hide clutter categories (Scope Boxes, Levels, Grids, Reference Planes)
        hide_clutter_categories(view, diagnostics)

        # Apply Section Box
        section_bbox_xyz = _make_section_bounding_box_xyz(framing_plan.section_box)
        view.SetSectionBox(section_bbox_xyz)
        view.IsSectionBoxActive = True

        # Apply Camera Orientation: ViewOrientation3D(eyePosition, upDirection, forwardDirection)
        if (
            framing_plan.eye_position is not None
            and framing_plan.up_vector is not None
            and framing_plan.forward_vector is not None
        ):
            orientation = _make_view_orientation_3d(
                framing_plan.eye_position,
                framing_plan.up_vector,
                framing_plan.forward_vector,
            )
            view.SetOrientation(orientation)

        # Apply Mode-Specific Visibility / Isolation Honestly
        all_link_ids = _collect_all_link_instance_ids(host_doc)
        participating_link_ids = set()
        for r in (res_a, res_b):
            if r is not None and getattr(r, "is_resolved", False) and getattr(r, "is_linked", False):
                lid = getattr(r, "link_instance_id", None)
                if lid is not None:
                    participating_link_ids.add(int(lid))

        # Create color highlights BEFORE isolation so the proxy shapes of linked participants
        # can be included in the isolate set (otherwise isolation would hide them).
        proxy_ids = []
        _remove_clash_highlight_filters(view, host_doc)
        if highlight_elements:
            _highlight_participant(
                view, host_doc, res_a, r=220, g=30, b=30, label="Item1",
                diagnostics=diagnostics, proxy_ids=proxy_ids,
            )
            _highlight_participant(
                view, host_doc, res_b, r=0, g=200, b=0, label="Item2",
                diagnostics=diagnostics, proxy_ids=proxy_ids,
            )

        if isolation_method == IsolationMethod.CONTEXT_SECTION_BOX_ONLY or mode == "context":
            # Mode B: Keep surrounding context visible inside section box; restore all categories & links
            restore_context_visibility(
                view, host_doc, all_link_ids=all_link_ids, diagnostics=diagnostics
            )
            if participating_link_ids and hasattr(view, "UnhideElements"):
                try:
                    view.UnhideElements(
                        _make_element_id_collection(sorted(participating_link_ids))
                    )
                except Exception:
                    pass

        elif isolation_method == IsolationMethod.NATIVE_ELEMENT_ISOLATION:
            # Mode A (Host-vs-Host): Only pass host ElementIds to IsolateElementsTemporary
            host_ids_to_isolate = []
            for r in (res_a, res_b):
                if (
                    r is not None
                    and getattr(r, "is_resolved", False)
                    and not getattr(r, "is_linked", False)
                ):
                    eid = getattr(r, "resolved_element_id", None)
                    if eid is not None and int(eid) not in host_ids_to_isolate:
                        host_ids_to_isolate.append(int(eid))

            if host_ids_to_isolate:
                for pid in proxy_ids:
                    if pid not in host_ids_to_isolate:
                        host_ids_to_isolate.append(pid)
                id_collection = _make_element_id_collection(host_ids_to_isolate)
                view.IsolateElementsTemporary(id_collection)
                isolated_host_ids = list(host_ids_to_isolate)
                if hasattr(view, "ConvertTemporaryHideIsolateToPermanent"):
                    try:
                        view.ConvertTemporaryHideIsolateToPermanent()
                    except Exception as ex:
                        diagnostics.append(
                            "Notice: ConvertTemporaryHideIsolateToPermanent failed ({0}); temporary isolation remains active.".format(
                                ex
                            )
                        )

        elif isolation_method == IsolationMethod.LINK_CATEGORY_AND_SECTION_BOX:
            # Mode A involving linked element(s):
            # Step 1: Isolate the host participant(s) AND the participating RevitLinkInstance(s) together,
            # then convert to permanent. This cleanly hides ALL other host elements (walls, floors, clutter)
            # AND hides all unrelated link instances (facade, landscape, etc.) in one fell swoop!
            host_ids_to_isolate = []
            for r in (res_a, res_b):
                if (
                    r is not None
                    and getattr(r, "is_resolved", False)
                    and not getattr(r, "is_linked", False)
                ):
                    eid = getattr(r, "resolved_element_id", None)
                    if eid is not None and int(eid) not in host_ids_to_isolate:
                        host_ids_to_isolate.append(int(eid))

            elements_to_isolate = list(host_ids_to_isolate)
            for pid in proxy_ids:
                if pid not in elements_to_isolate:
                    elements_to_isolate.append(pid)
            for lid in sorted(participating_link_ids):
                if lid not in elements_to_isolate:
                    elements_to_isolate.append(lid)

            if elements_to_isolate and hasattr(view, "IsolateElementsTemporary"):
                try:
                    id_col = _make_element_id_collection(elements_to_isolate)
                    view.IsolateElementsTemporary(id_col)
                    isolated_host_ids = list(host_ids_to_isolate)
                    if hasattr(view, "ConvertTemporaryHideIsolateToPermanent"):
                        try:
                            view.ConvertTemporaryHideIsolateToPermanent()
                        except Exception:
                            pass
                except Exception as ex:
                    diagnostics.append("Notice: Participant isolation failed: {0}".format(ex))

            # Step 2: Ensure participating link instances are explicitly unhidden
            if participating_link_ids and hasattr(view, "UnhideElements"):
                try:
                    view.UnhideElements(
                        _make_element_id_collection(sorted(participating_link_ids))
                    )
                except Exception:
                    pass

            # Step 3: Hide all unrelated link instances one by one AFTER permanent conversion
            # Doing this individually prevents a single unloadable/pinned link from aborting the others!
            unrelated_link_ids = [
                int(lid)
                for lid in all_link_ids
                if int(lid) not in participating_link_ids
            ]
            hidden_link_ids = []
            if unrelated_link_ids and hasattr(view, "HideElements"):
                for lid in unrelated_link_ids:
                    try:
                        eid = create_revit_element_id(lid)
                        elem = host_doc.GetElement(eid) if hasattr(host_doc, "GetElement") else None
                        if elem is not None and hasattr(elem, "CanBeHidden"):
                            if not elem.CanBeHidden(view):
                                continue
                        view.HideElements(_make_element_id_collection([lid]))
                        hidden_link_ids.append(lid)
                    except Exception as ex:
                        diagnostics.append(
                            "Warning: Could not hide unrelated link instance {0}: {1}".format(lid, ex)
                        )

            diagnostics.append(
                "Applied LINK_CATEGORY_AND_SECTION_BOX: Tightly cropped section box to clashing pair, isolated host participant(s), and hid {0} unrelated link instance(s).".format(
                    len(hidden_link_ids)
                )
            )

        # (Highlights were already applied before isolation, see above.)
        if highlight_elements:
            _ensure_proxies_visible(view, proxy_ids)
        else:
            # If highlighting is disabled, ensure host participants have clean default overrides
            DB = _get_revit_db()
            for r in (res_a, res_b):
                if r is not None and getattr(r, "is_resolved", False) and not getattr(r, "is_linked", False):
                    eid = getattr(r, "resolved_element_id", None)
                    if eid is not None and hasattr(view, "SetElementOverrides") and DB is not None:
                        try:
                            view.SetElementOverrides(create_revit_element_id(eid), DB.OverrideGraphicSettings())
                        except Exception:
                            pass

        if mode == "pair":
            # Synchronize Revit Link Visibility in Visibility/Graphics (VV)
            # Only links affiliated with the clash remain switched ON; all others are switched OFF in VV.
            sync_link_visibility_in_vv(
                view, host_doc, res_a, res_b, mode=mode, diagnostics=diagnostics
            )

            # Isolate clashing participant categories: hide all non-participating Model categories in view
            # Ensures ONLY the 2 clashing elements show in the isolated view!
            isolate_clash_participant_categories(
                view, host_doc, res_a, res_b, mode=mode, diagnostics=diagnostics
            )
            # That step hides every non-participating model category (incl. Generic Models, which
            # holds the color proxies), so re-assert proxy visibility as the very last view change.
            if highlight_elements:
                _ensure_proxies_visible(view, proxy_ids)
        # Assign View Name safely (renaming view if mode changed and desired name is available)
        if getattr(view, "Name", None) != resolved_name:
            other_views = _collect_host_view3ds(host_doc)
            name_taken = False
            for ov in other_views:
                if ov is not view and getattr(ov, "Name", "") == resolved_name:
                    name_taken = True
                    break
            if not name_taken:
                try:
                    view.Name = resolved_name
                except Exception as ex:
                    diagnostics.append(
                        "Notice: Could not rename view to '{0}': {1}".format(resolved_name, ex)
                    )

        # Write Tool Ownership Metadata
        meta_payload = {
            "ToolId": CLASHVIEW_TOOL_ID,
            "SchemaVersion": CLASHVIEW_SCHEMA_VERSION,
            "Mode": mode,
            "TestName": test_name or "",
            "ClashName": clash_name or "",
            "ClashGuid": clash_guid or "",
            "SourceFile": source_file or "",
            "FramingMethod": framing_method,
            "IsolationMethod": isolation_method,
            "CoordFrameStatus": coord_frame_status,
            "ParticipantAId": (
                getattr(res_a, "resolved_element_id", "") if res_a else ""
            ),
            "ParticipantBId": (
                getattr(res_b, "resolved_element_id", "") if res_b else ""
            ),
            "LinkInstanceAId": (
                getattr(res_a, "link_instance_id", "") if res_a else ""
            ),
            "LinkInstanceBId": (
                getattr(res_b, "link_instance_id", "") if res_b else ""
            ),
        }
        meta_ok, meta_diags = write_view_metadata(view, meta_payload)
        diagnostics.extend(meta_diags)

        tx.Commit()

        return ViewCreationResult(
            succeeded=True,
            action_taken=action_taken,
            view=view,
            view_id=get_element_id_value(getattr(view, "Id", None)),
            view_name=resolved_name,
            mode=mode,
            framing_method=framing_method,
            isolation_method=isolation_method,
            coord_frame_status=coord_frame_status,
            hidden_link_ids=hidden_link_ids,
            isolated_host_ids=isolated_host_ids,
            metadata_written=meta_ok,
            diagnostics=diagnostics,
        )

    except Exception as ex:
        if tx is not None:
            try:
                has_started = (
                    tx.HasStarted()
                    if hasattr(tx, "HasStarted") and callable(tx.HasStarted)
                    else getattr(tx, "started", True)
                )
                has_ended = (
                    tx.HasEnded()
                    if hasattr(tx, "HasEnded") and callable(tx.HasEnded)
                    else getattr(tx, "ended", False)
                )
                if has_started and not has_ended:
                    tx.RollBack()
            except Exception:
                pass

        diagnostics.append(
            "Transaction rolled back due to error during 3D view creation/update: {0}".format(
                ex
            )
        )
        return ViewCreationResult(
            succeeded=False,
            action_taken="ABORTED",
            view=None,
            view_id=None,
            view_name=resolved_name,
            mode=mode,
            framing_method=framing_method,
            isolation_method=isolation_method,
            coord_frame_status=coord_frame_status,
            hidden_link_ids=[],
            isolated_host_ids=[],
            metadata_written=False,
            diagnostics=diagnostics,
        )
