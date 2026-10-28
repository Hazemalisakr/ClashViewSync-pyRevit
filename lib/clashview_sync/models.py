# -*- coding: utf-8 -*-
"""Data models for ClashView Sync.

Defines unified data structures representing Navisworks clash tests,
grouped and individual clash results, participants, spatial coordinates,
camera viewpoints, and parsing diagnostics.

Compatible with Python 2.7 (pyRevit IronPython) and Python 3.x.
"""
from __future__ import absolute_import, division, print_function

import sys

# Python 2/3 string & integer compatibility
if sys.version_info[0] >= 3:
    string_types = (str,)
    integer_types = (int,)
else:
    string_types = (basestring,)  # noqa: F821
    integer_types = (int, long)    # noqa: F821


class ClashPoint(object):
    """Represents a 3D coordinate point with declared unit context."""

    def __init__(self, x=0.0, y=0.0, z=0.0, units="m"):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        self.units = str(units).strip().lower() if units else "m"

    def to_tuple(self):
        """Returns coordinate components as an (x, y, z) tuple."""
        return (self.x, self.y, self.z)

    def to_dict(self):
        """Serializes point data to a dictionary."""
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "units": self.units,
        }

    def __repr__(self):
        return "ClashPoint(x={0:.4f}, y={1:.4f}, z={2:.4f}, units='{3}')".format(
            self.x, self.y, self.z, self.units
        )

    def __eq__(self, other):
        if not isinstance(other, ClashPoint):
            return False
        return (
            abs(self.x - other.x) < 1e-7
            and abs(self.y - other.y) < 1e-7
            and abs(self.z - other.z) < 1e-7
            and self.units == other.units
        )


class ClashParticipant(object):
    """Represents one of the clashing items (Item 1 or Item 2)."""

    def __init__(
        self,
        model_name=None,
        model_path=None,
        element_id=None,
        element_id_raw=None,
        unique_id=None,
        item_name=None,
        category=None,
        layer=None,
        path_nodes=None,
        smarttags=None,
        attributes=None,
        diagnostics=None,
    ):
        self.model_name = str(model_name).strip() if model_name else None
        self.model_path = str(model_path).strip() if model_path else None

        # Element ID handling: explicit integer vs raw representation
        self.element_id = int(element_id) if element_id is not None else None
        self.element_id_raw = (
            str(element_id_raw).strip() if element_id_raw is not None else None
        )
        self.unique_id = str(unique_id).strip() if unique_id else None

        self.item_name = str(item_name).strip() if item_name else None
        self.category = str(category).strip() if category else None
        self.layer = str(layer).strip() if layer else None

        # Hierarchy / path in the Navisworks selection tree
        self.path_nodes = list(path_nodes) if path_nodes else []

        # Raw extracted metadata dictionaries
        self.smarttags = dict(smarttags) if smarttags else {}
        self.attributes = dict(attributes) if attributes else {}

        # Parsing or resolution issues specific to this item
        self.diagnostics = list(diagnostics) if diagnostics else []
        self.resolution = None

    @property
    def is_valid_element_id(self):
        """Returns True if a positive Revit Element ID is present."""
        return self.element_id is not None and self.element_id > 0

    def to_dict(self):
        """Serializes participant data to a dictionary."""
        return {
            "model_name": self.model_name,
            "model_path": self.model_path,
            "element_id": self.element_id,
            "element_id_raw": self.element_id_raw,
            "unique_id": self.unique_id,
            "item_name": self.item_name,
            "category": self.category,
            "layer": self.layer,
            "path_nodes": self.path_nodes,
            "smarttags": self.smarttags,
            "attributes": self.attributes,
            "diagnostics": self.diagnostics,
            "is_valid_element_id": self.is_valid_element_id,
            "resolution": self.resolution.to_dict() if self.resolution and hasattr(self.resolution, "to_dict") else None,
        }

    def __repr__(self):
        return (
            "ClashParticipant(model='{0}', id={1}, name='{2}', category='{3}')".format(
                self.model_name or "",
                self.element_id,
                self.item_name or "",
                self.category or "",
            )
        )


class CameraViewpoint(object):
    """Represents a Navisworks saved camera viewpoint orientation and framing."""

    def __init__(
        self,
        name=None,
        guid=None,
        projection="persp",
        position=None,
        quaternion=None,
        up_vector=None,
        forward_vector=None,
        focal_distance=None,
        fov=None,
        aspect=None,
        view_height=None,
        near_clip=None,
        far_clip=None,
        units="m",
        units_verified=True,
    ):
        self.name = str(name).strip() if name else None
        self.guid = str(guid).strip() if guid else None

        # Projection: 'persp' (perspective) or 'ortho' (orthographic)
        proj_str = str(projection).strip().lower() if projection else "persp"
        self.projection = "ortho" if "ortho" in proj_str else "persp"

        # Position (eye)
        if isinstance(position, ClashPoint):
            self.position = position
        elif isinstance(position, (tuple, list)) and len(position) >= 3:
            self.position = ClashPoint(position[0], position[1], position[2], units=units)
        else:
            self.position = None

        # Quaternion rotation (a, b, c, d) where a,b,c are vector (x,y,z) and d is scalar (w)
        if isinstance(quaternion, (tuple, list)) and len(quaternion) >= 4:
            self.quaternion = (
                float(quaternion[0]),
                float(quaternion[1]),
                float(quaternion[2]),
                float(quaternion[3]),
            )
        else:
            self.quaternion = None

        # Derived or explicit vectors
        self.up_vector = tuple(float(v) for v in up_vector) if up_vector else None
        self.forward_vector = (
            tuple(float(v) for v in forward_vector) if forward_vector else None
        )

        self.focal_distance = (
            float(focal_distance) if focal_distance is not None else None
        )
        self.fov = float(fov) if fov is not None else None
        self.aspect = float(aspect) if aspect is not None else None
        self.view_height = float(view_height) if view_height is not None else None
        self.near_clip = float(near_clip) if near_clip is not None else None
        self.far_clip = float(far_clip) if far_clip is not None else None
        self.units = str(units).strip().lower() if units else "m"
        self.units_verified = bool(units_verified)

    def to_dict(self):
        """Serializes viewpoint data to a dictionary."""
        return {
            "name": self.name,
            "guid": self.guid,
            "projection": self.projection,
            "position": self.position.to_dict() if self.position else None,
            "quaternion": self.quaternion,
            "up_vector": self.up_vector,
            "forward_vector": self.forward_vector,
            "focal_distance": self.focal_distance,
            "fov": self.fov,
            "aspect": self.aspect,
            "view_height": self.view_height,
            "near_clip": self.near_clip,
            "far_clip": self.far_clip,
            "units": self.units,
            "units_verified": self.units_verified,
        }

    def __repr__(self):
        return "CameraViewpoint(name='{0}', proj='{1}', guid='{2}')".format(
            self.name or "", self.projection, self.guid or ""
        )


class ClashResultItem(object):
    """Represents a single clash result between two participants."""

    def __init__(
        self,
        name=None,
        guid=None,
        status="New",
        distance=0.0,
        description=None,
        result_status=None,
        parent_group=None,
        parent_group_guid=None,
        clash_point=None,
        participant_a=None,
        participant_b=None,
        all_participants=None,
        viewpoint=None,
        comments=None,
        diagnostics=None,
    ):
        self.name = str(name).strip() if name else None
        self.guid = str(guid).strip() if guid else None
        self.status = str(status).strip() if status else "New"
        self.distance = float(distance) if distance is not None else 0.0
        self.description = str(description).strip() if description else None
        self.result_status = str(result_status).strip() if result_status else self.status

        # Group nesting
        self.parent_group = str(parent_group).strip() if parent_group else None
        self.parent_group_guid = (
            str(parent_group_guid).strip() if parent_group_guid else None
        )

        self.clash_point = clash_point

        self.participant_a = participant_a
        self.participant_b = participant_b

        if all_participants:
            self.all_participants = list(all_participants)
        else:
            pts = []
            if participant_a:
                pts.append(participant_a)
            if participant_b:
                pts.append(participant_b)
            self.all_participants = pts

        self.viewpoint = viewpoint
        self.comments = list(comments) if comments else []
        self.diagnostics = list(diagnostics) if diagnostics else []

    @property
    def is_grouped(self):
        """Returns True if this clash result belongs to a group."""
        return bool(self.parent_group)

    @property
    def has_both_element_ids(self):
        """Returns True if both participants have valid positive Element IDs."""
        return bool(
            self.participant_a
            and self.participant_a.is_valid_element_id
            and self.participant_b
            and self.participant_b.is_valid_element_id
        )

    def to_dict(self):
        """Serializes clash result to a dictionary."""
        return {
            "name": self.name,
            "guid": self.guid,
            "status": self.status,
            "result_status": self.result_status,
            "distance": self.distance,
            "description": self.description,
            "parent_group": self.parent_group,
            "parent_group_guid": self.parent_group_guid,
            "clash_point": self.clash_point.to_dict() if self.clash_point else None,
            "participant_a": self.participant_a.to_dict() if self.participant_a else None,
            "participant_b": self.participant_b.to_dict() if self.participant_b else None,
            "all_participants": [p.to_dict() for p in self.all_participants],
            "viewpoint": self.viewpoint.to_dict() if self.viewpoint else None,
            "comments": self.comments,
            "diagnostics": self.diagnostics,
            "is_grouped": self.is_grouped,
            "has_both_element_ids": self.has_both_element_ids,
        }

    def __repr__(self):
        return (
            "ClashResultItem(name='{0}', status='{1}', group='{2}', "
            "partA={3}, partB={4})".format(
                self.name or "",
                self.status,
                self.parent_group or "",
                getattr(self.participant_a, "element_id", None),
                getattr(self.participant_b, "element_id", None),
            )
        )


class ClashGroup(object):
    """Represents a grouped collection of clash results."""

    def __init__(
        self,
        name=None,
        guid=None,
        status="New",
        description=None,
        clash_point=None,
        results=None,
        diagnostics=None,
    ):
        self.name = str(name).strip() if name else None
        self.guid = str(guid).strip() if guid else None
        self.status = str(status).strip() if status else "New"
        self.description = str(description).strip() if description else None
        self.clash_point = clash_point
        self.results = list(results) if results else []
        self.diagnostics = list(diagnostics) if diagnostics else []

    @property
    def count(self):
        """Number of clash results inside this group."""
        return len(self.results)

    def to_dict(self):
        """Serializes group to a dictionary."""
        return {
            "name": self.name,
            "guid": self.guid,
            "status": self.status,
            "description": self.description,
            "clash_point": self.clash_point.to_dict() if self.clash_point else None,
            "results": [r.to_dict() for r in self.results],
            "diagnostics": self.diagnostics,
            "count": self.count,
        }

    def __repr__(self):
        return "ClashGroup(name='{0}', count={1}, status='{2}')".format(
            self.name or "", self.count, self.status
        )


class ClashTestGroup(object):
    """Represents a clash test containing results and groups."""

    def __init__(
        self,
        name=None,
        guid=None,
        test_type="hard",
        tolerance=0.001,
        status="Active",
        results=None,
        groups=None,
        diagnostics=None,
    ):
        self.name = str(name).strip() if name else None
        self.guid = str(guid).strip() if guid else None
        self.test_type = str(test_type).strip() if test_type else "hard"
        self.tolerance = float(tolerance) if tolerance is not None else 0.001
        self.status = str(status).strip() if status else "Active"

        # List of all individual ClashResultItems (flat, including those inside groups)
        self.results = list(results) if results else []

        # List of ClashGroup objects
        self.groups = list(groups) if groups else []

        self.diagnostics = list(diagnostics) if diagnostics else []

    @property
    def total_results_count(self):
        """Total number of individual clash results in this test."""
        return len(self.results)

    def to_dict(self):
        """Serializes clash test to a dictionary."""
        return {
            "name": self.name,
            "guid": self.guid,
            "test_type": self.test_type,
            "tolerance": self.tolerance,
            "status": self.status,
            "results": [r.to_dict() for r in self.results],
            "groups": [g.to_dict() for g in self.groups],
            "diagnostics": self.diagnostics,
            "total_results_count": self.total_results_count,
        }

    def __repr__(self):
        return "ClashTestGroup(name='{0}', results={1}, groups={2})".format(
            self.name or "", len(self.results), len(self.groups)
        )


class ClashSyncPayload(object):
    """Root container representing the complete output of an XML import or merge."""

    def __init__(
        self,
        source_file=None,
        source_format="clash_xml",
        units="m",
        tests=None,
        viewpoints=None,
        diagnostics=None,
        units_verified=True,
    ):
        self.source_file = str(source_file) if source_file else None
        self.source_format = str(source_format) if source_format else "clash_xml"
        self.units = str(units).strip().lower() if units else "m"
        self.units_verified = bool(units_verified)

        self.tests = list(tests) if tests else []
        self.viewpoints = list(viewpoints) if viewpoints else []
        self.diagnostics = list(diagnostics) if diagnostics else []

    @property
    def total_clashes_count(self):
        """Calculates total clashes across all tests."""
        return sum(len(t.results) for t in self.tests)

    @property
    def total_groups_count(self):
        """Calculates total groups across all tests."""
        return sum(len(t.groups) for t in self.tests)

    def summary(self):
        """Returns a high-level summary dictionary of the payload contents."""
        return {
            "source_file": self.source_file,
            "source_format": self.source_format,
            "units": self.units,
            "units_verified": self.units_verified,
            "test_count": len(self.tests),
            "clash_count": self.total_clashes_count,
            "group_count": self.total_groups_count,
            "viewpoint_count": len(self.viewpoints),
            "diagnostic_count": len(self.diagnostics),
        }

    def to_dict(self):
        """Serializes full payload to a dictionary."""
        return {
            "source_file": self.source_file,
            "source_format": self.source_format,
            "units": self.units,
            "units_verified": self.units_verified,
            "summary": self.summary(),
            "tests": [t.to_dict() for t in self.tests],
            "viewpoints": [v.to_dict() for v in self.viewpoints],
            "diagnostics": self.diagnostics,
        }

    def __repr__(self):
        return (
            "ClashSyncPayload(tests={0}, clashes={1}, viewpoints={2}, "
            "units='{3}')".format(
                len(self.tests),
                self.total_clashes_count,
                len(self.viewpoints),
                self.units,
            )
        )
