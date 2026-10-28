# -*- coding: utf-8 -*-
"""Unit tests for clashview_sync.coordinate_transformer."""
from __future__ import absolute_import, division, print_function

import math
import os
import sys
import unittest

_LIB_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        os.path.join(os.path.dirname(__file__), "..", "lib"),
    )
)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from clashview_sync.coordinate_transformer import (
    BoundingBox3D,
    CoordinateFrameStatus,
    CoordinateTransform,
    FramingMethod,
    IsolationMethod,
    Quaternion,
    Vector3,
    calculate_clash_framing,
    calculate_fallback_framing,
    convert_length,
    evaluate_clash_geometry_corroboration,
    extract_element_host_bbox,
    get_feet_scale_factor,
    validate_and_orthogonalize_camera,
)
from clashview_sync.models import CameraViewpoint, ClashParticipant, ClashPoint
from clashview_sync.element_resolver import (
    ParticipantResolution,
    ResolutionStatus,
    TargetDocumentType,
)


class TestCoordinateTransformer(unittest.TestCase):
    """Tests vector algebra, quaternions, unit conversion, and transforms."""

    def test_unit_conversion_factors(self):
        # 1 meter is exactly 1.0 / 0.3048 feet
        self.assertAlmostEqual(convert_length(1.0, "m", "ft"), 3.280839895013123, places=7)
        # 1000 mm = 1 m
        self.assertAlmostEqual(convert_length(1000.0, "mm", "m"), 1.0, places=7)
        # 1000 mm to feet
        self.assertAlmostEqual(convert_length(1000.0, "mm", "ft"), 3.280839895013123, places=7)
        # 12 inches = 1 foot
        self.assertAlmostEqual(convert_length(12.0, "in", "ft"), 1.0, places=7)
        # 3 feet = 1 yard
        self.assertAlmostEqual(convert_length(1.0, "yrd", "ft"), 3.0, places=7)
        # 1 mile = 5280 feet
        self.assertAlmostEqual(convert_length(1.0, "mi", "ft"), 5280.0, places=7)

    def test_unsupported_unit_raises(self):
        with self.assertRaises(ValueError):
            get_feet_scale_factor("unknown_unit_xyz")

    def test_vector3_arithmetic(self):
        v1 = Vector3(1.0, 2.0, 3.0)
        v2 = Vector3(4.0, 5.0, 6.0)

        # Addition & subtraction
        add_res = v1 + v2
        self.assertTrue(add_res.is_almost_equal(Vector3(5.0, 7.0, 9.0)))
        sub_res = v2 - v1
        self.assertTrue(sub_res.is_almost_equal(Vector3(3.0, 3.0, 3.0)))

        # Scalar multiplication & division
        mul_res = v1 * 2.0
        self.assertTrue(mul_res.is_almost_equal(Vector3(2.0, 4.0, 6.0)))
        div_res = v2 / 2.0
        self.assertTrue(div_res.is_almost_equal(Vector3(2.0, 2.5, 3.0)))

        # Unary negation
        neg_res = -v1
        self.assertTrue(neg_res.is_almost_equal(Vector3(-1.0, -2.0, -3.0)))

        # Division by zero
        with self.assertRaises(ZeroDivisionError):
            _ = v1 / 0.0

    def test_vector3_products_and_length(self):
        v1 = Vector3(1.0, 0.0, 0.0)
        v2 = Vector3(0.0, 1.0, 0.0)

        # Dot product of orthogonal unit vectors is 0
        self.assertAlmostEqual(v1.dot(v2), 0.0, places=7)

        # Cross product of X and Y is Z
        cross_res = v1.cross(v2)
        self.assertTrue(cross_res.is_almost_equal(Vector3(0.0, 0.0, 1.0)))

        # Length of 3-4-0 vector is 5
        v3 = Vector3(3.0, 4.0, 0.0)
        self.assertAlmostEqual(v3.length(), 5.0, places=7)
        norm_v3 = v3.normalize()
        self.assertAlmostEqual(norm_v3.length(), 1.0, places=7)
        self.assertTrue(norm_v3.is_almost_equal(Vector3(0.6, 0.8, 0.0)))

        # Normalization of near-zero vector raises ValueError
        with self.assertRaises(ValueError):
            Vector3(0.0, 0.0, 0.0).normalize()

    def test_quaternion_identity_camera_vectors(self):
        # Identity quaternion (a=0, b=0, c=0, d=1)
        q_id = Quaternion(0.0, 0.0, 0.0, 1.0)
        self.assertTrue(q_id.is_normalized())

        forward, up, right = q_id.to_camera_vectors()
        # Default Navisworks camera frame:
        # forward is (0, 0, -1), up is (0, 1, 0), right is (1, 0, 0)
        self.assertTrue(forward.is_almost_equal(Vector3(0.0, 0.0, -1.0)))
        self.assertTrue(up.is_almost_equal(Vector3(0.0, 1.0, 0.0)))
        self.assertTrue(right.is_almost_equal(Vector3(1.0, 0.0, 0.0)))

        # Verify mutual orthogonality
        self.assertAlmostEqual(forward.dot(up), 0.0, places=7)
        self.assertAlmostEqual(forward.dot(right), 0.0, places=7)
        self.assertAlmostEqual(up.dot(right), 0.0, places=7)

    def test_quaternion_90_deg_yaw(self):
        # 90 degrees around +Z axis:
        # half-angle = 45 deg, sin(45)=cos(45)=sqrt(2)/2 ~= 0.70710678
        half = math.sin(math.pi / 4.0)
        q_yaw = Quaternion(0.0, 0.0, half, half)
        self.assertTrue(q_yaw.is_normalized())

        forward, up, right = q_yaw.to_camera_vectors()
        # Forward was (0,0,-1); rotating around Z leaves Z unchanged -> (0,0,-1)
        self.assertTrue(forward.is_almost_equal(Vector3(0.0, 0.0, -1.0)))
        # Up was (0,1,0); rotated 90 deg around Z points along -X -> (-1, 0, 0)
        self.assertTrue(up.is_almost_equal(Vector3(-1.0, 0.0, 0.0)))
        # Right was (1,0,0); rotated 90 deg around Z points along +Y -> (0, 1, 0)
        self.assertTrue(right.is_almost_equal(Vector3(0.0, 1.0, 0.0)))

        # Check orthogonality
        self.assertAlmostEqual(forward.dot(up), 0.0, places=7)
        self.assertAlmostEqual(up.dot(right), 0.0, places=7)

    def test_quaternion_arbitrary_orthogonality(self):
        # Test an arbitrary non-trivial quaternion
        q_raw = Quaternion(0.2, 0.4, 0.6, 0.8)
        q = q_raw.normalize()
        self.assertTrue(q.is_normalized())

        forward, up, right = q.to_camera_vectors()
        self.assertAlmostEqual(forward.length(), 1.0, places=6)
        self.assertAlmostEqual(up.length(), 1.0, places=6)
        self.assertAlmostEqual(right.length(), 1.0, places=6)

        # Dot products between all pairs must be zero
        self.assertAlmostEqual(forward.dot(up), 0.0, places=6)
        self.assertAlmostEqual(forward.dot(right), 0.0, places=6)
        self.assertAlmostEqual(up.dot(right), 0.0, places=6)

    def test_validate_and_orthogonalize_camera(self):
        # Forward looking down -Z
        f = Vector3(0.0, 0.0, -1.0)
        # Up intentionally skewed at 45 degrees towards forward: (0, 1, -1)
        u_skewed = Vector3(0.0, 1.0, -1.0)

        # Gram-Schmidt should project out the forward component
        f_norm, u_ortho, r_norm, was_adjusted = validate_and_orthogonalize_camera(
            f, u_skewed, tol=1e-4
        )
        self.assertTrue(was_adjusted)
        self.assertAlmostEqual(f_norm.dot(u_ortho), 0.0, places=7)
        self.assertTrue(u_ortho.is_almost_equal(Vector3(0.0, 1.0, 0.0)))
        self.assertTrue(r_norm.is_almost_equal(Vector3(1.0, 0.0, 0.0)))

    def test_coordinate_transform_and_inverse(self):
        # Transform: Scale by 3.28084 (m -> ft), translate by (10, 20, 5)
        scale = 3.280839895013123
        t = CoordinateTransform(
            scale=scale,
            translation=Vector3(10.0, 20.0, 5.0),
        )
        self.assertFalse(t.is_identity())

        pt_orig = Vector3(5.0, -3.0, 2.0)
        pt_transformed = t.transform_point(pt_orig)

        # Expected: pt * scale + T
        self.assertAlmostEqual(pt_transformed.x, 5.0 * scale + 10.0, places=5)
        self.assertAlmostEqual(pt_transformed.y, -3.0 * scale + 20.0, places=5)
        self.assertAlmostEqual(pt_transformed.z, 2.0 * scale + 5.0, places=5)

        # Inverse transform must restore pt_orig
        t_inv = t.inverse()
        pt_restored = t_inv.transform_point(pt_transformed)
        self.assertTrue(pt_restored.is_almost_equal(pt_orig, tol=1e-6))

    def test_calculate_fallback_framing(self):
        clash_center = Vector3(10.0, 20.0, 30.0)
        margin = 5.0
        framing = calculate_fallback_framing(clash_center, margin_ft=margin)

        forward = framing["forward_vector"]
        up = framing["up_vector"]
        eye = framing["eye_position"]
        box_min = framing["box_min"]
        box_max = framing["box_max"]

        # Forward and up must be orthogonal unit vectors
        self.assertAlmostEqual(forward.length(), 1.0, places=6)
        self.assertAlmostEqual(up.length(), 1.0, places=6)
        self.assertAlmostEqual(forward.dot(up), 0.0, places=6)

        # Eye position must look towards clash_center
        direction_to_center = (clash_center - eye).normalize()
        self.assertTrue(direction_to_center.is_almost_equal(forward, tol=1e-5))

        # Section box bounds must enclose clash center with margin
        self.assertTrue(box_min.is_almost_equal(Vector3(5.0, 15.0, 25.0)))
        self.assertTrue(box_max.is_almost_equal(Vector3(15.0, 25.0, 35.0)))

    def test_bounding_box_3d_operations_and_validation(self):
        # Inverted coordinates must raise ValueError
        with self.assertRaises(ValueError):
            BoundingBox3D(Vector3(10.0, 0.0, 0.0), Vector3(5.0, 1.0, 1.0))

        box = BoundingBox3D(Vector3(0.0, 0.0, 0.0), Vector3(2.0, 4.0, 6.0))
        self.assertTrue(box.center.is_almost_equal(Vector3(1.0, 2.0, 3.0)))
        self.assertTrue(box.size.is_almost_equal(Vector3(2.0, 4.0, 6.0)))
        self.assertAlmostEqual(box.volume, 48.0, places=6)
        self.assertFalse(box.is_degenerate())
        self.assertEqual(len(box.corners()), 8)

        # Degenerate zero-thickness box
        flat_box = BoundingBox3D(Vector3(0.0, 0.0, 5.0), Vector3(2.0, 2.0, 5.0))
        self.assertTrue(flat_box.is_degenerate())

        # Expand and enforce_min_dimensions
        expanded = box.expand(1.0)
        self.assertTrue(expanded.min_pt.is_almost_equal(Vector3(-1.0, -1.0, -1.0)))
        self.assertTrue(expanded.max_pt.is_almost_equal(Vector3(3.0, 5.0, 7.0)))

        small_box = BoundingBox3D(Vector3(9.5, 19.5, 29.5), Vector3(10.5, 20.5, 30.5))
        min_enforced = small_box.enforce_min_dimensions(min_span_ft=4.0)
        self.assertTrue(min_enforced.center.is_almost_equal(Vector3(10.0, 20.0, 30.0)))
        self.assertTrue(min_enforced.size.is_almost_equal(Vector3(4.0, 4.0, 4.0)))

        # Union, intersection, distance, point containment
        other = BoundingBox3D(Vector3(5.0, 0.0, 0.0), Vector3(7.0, 4.0, 6.0))
        self.assertFalse(box.intersects(other))
        self.assertAlmostEqual(box.min_distance_to(other), 3.0, places=6)
        self.assertTrue(box.intersects(other, tolerance_ft=3.1))

        merged = box.union(other)
        self.assertTrue(merged.min_pt.is_almost_equal(Vector3(0.0, 0.0, 0.0)))
        self.assertTrue(merged.max_pt.is_almost_equal(Vector3(7.0, 4.0, 6.0)))
        self.assertTrue(merged.contains_point(Vector3(3.5, 2.0, 3.0)))
        self.assertFalse(merged.contains_point(Vector3(10.0, 2.0, 3.0)))

    def test_8_corner_bbox_transformations_rotations_and_mirror(self):
        # Asymmetric box: X in [1, 5] (len 4), Y in [2, 12] (len 10), Z in [0, 3]
        box = BoundingBox3D(Vector3(1.0, 2.0, 0.0), Vector3(5.0, 12.0, 3.0))

        # 1. 90-degree CCW rotation around Z: (x, y, z) -> (-y, x, z)
        rot_90 = (
            (0.0, -1.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0),
        )
        t_90 = CoordinateTransform(
            rotation_matrix=rot_90,
            translation=Vector3(100.0, 200.0, 10.0),
        )
        # Note: Naive 2-point transform of min (1,2,0) -> (-2,1,0) and max (5,12,3) -> (-12,5,3)
        # would invert X (-2 > -12)! 8-corner transform must produce X in [100-12, 100-2] = [88, 98].
        box_90 = t_90.transform_bounding_box(box)
        self.assertTrue(box_90.min_pt.is_almost_equal(Vector3(88.0, 201.0, 10.0)))
        self.assertTrue(box_90.max_pt.is_almost_equal(Vector3(98.0, 205.0, 13.0)))

        # Verify transform_vector ignores translation and scale
        t_scaled = CoordinateTransform(
            scale=3.0,
            rotation_matrix=rot_90,
            translation=Vector3(50.0, 60.0, 70.0),
        )
        vec_rot = t_scaled.transform_vector(Vector3(1.0, 0.0, 0.0))
        self.assertTrue(vec_rot.is_almost_equal(Vector3(0.0, 1.0, 0.0)))

        # 2. 180-degree rotation around Z: (x, y, z) -> (-x, -y, z)
        rot_180 = (
            (-1.0, 0.0, 0.0),
            (0.0, -1.0, 0.0),
            (0.0, 0.0, 1.0),
        )
        t_180 = CoordinateTransform(rotation_matrix=rot_180)
        box_180 = t_180.transform_bounding_box(box)
        self.assertTrue(box_180.min_pt.is_almost_equal(Vector3(-5.0, -12.0, 0.0)))
        self.assertTrue(box_180.max_pt.is_almost_equal(Vector3(-1.0, -2.0, 3.0)))

        # 3. 45-degree rotation around Z on a centered 2x2x2 box: [-1, 1] x [-1, 1]
        # Under 45-deg rotation, X and Y extents expand to [-sqrt(2), +sqrt(2)], whereas
        # a naive 2-point min/max transform of (-1,-1) and (1,1) yields X=0, Y in [-sqrt(2), +sqrt(2)]!
        c45 = math.cos(math.pi / 4.0)
        s45 = math.sin(math.pi / 4.0)
        rot_45 = (
            (c45, -s45, 0.0),
            (s45, c45, 0.0),
            (0.0, 0.0, 1.0),
        )
        t_45 = CoordinateTransform(rotation_matrix=rot_45)
        unit_box = BoundingBox3D(Vector3(-1.0, -1.0, 0.0), Vector3(1.0, 1.0, 2.0))
        box_45 = t_45.transform_bounding_box(unit_box)
        rt2 = math.sqrt(2.0)
        self.assertTrue(box_45.min_pt.is_almost_equal(Vector3(-rt2, -rt2, 0.0)))
        self.assertTrue(box_45.max_pt.is_almost_equal(Vector3(rt2, rt2, 2.0)))

        # 4. Mirror reflection across X axis: (x, y, z) -> (-x, y, z)
        mirror_x = (
            (-1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        )
        t_mirror = CoordinateTransform(rotation_matrix=mirror_x)
        box_mirrored = t_mirror.transform_bounding_box(box)
        self.assertTrue(box_mirrored.min_pt.is_almost_equal(Vector3(-5.0, 2.0, 0.0)))
        self.assertTrue(box_mirrored.max_pt.is_almost_equal(Vector3(-1.0, 12.0, 3.0)))

    def test_extract_element_host_bbox_sequential_transforms(self):
        # Mock Revit Transform with BasisX, BasisY, BasisZ, Origin
        class MockRevitTransform(object):
            def __init__(self, bx, by, bz, origin):
                self.BasisX = Vector3(*bx)
                self.BasisY = Vector3(*by)
                self.BasisZ = Vector3(*bz)
                self.Origin = Vector3(*origin)
                self.IsIdentity = False

        class MockBBoxXYZ(object):
            def __init__(self, min_pt, max_pt, transform=None):
                self.Min = Vector3(*min_pt)
                self.Max = Vector3(*max_pt)
                self.Transform = transform

        class MockElemWithBBox(object):
            def __init__(self, bbox):
                self._bbox = bbox

            def get_BoundingBox(self, view):
                return self._bbox

        class MockLinkWithTransform(object):
            def __init__(self, total_transform):
                self._xform = total_transform

            def GetTotalTransform(self):
                return self._xform

        # Local box: [0, 2] x [0, 4] x [0, 1]
        # 1. bbox.Transform translates by (+10, +20, +5) -> [10, 12] x [20, 24] x [5, 6]
        bbox_xform = MockRevitTransform(
            bx=(1.0, 0.0, 0.0),
            by=(0.0, 1.0, 0.0),
            bz=(0.0, 0.0, 1.0),
            origin=(10.0, 20.0, 5.0),
        )
        # 2. link_instance.GetTotalTransform rotates 90 deg around Z ((x,y)->(-y,x)) and translates by (+100, +50, 0)
        # Transformed X: -y + 100 for y in [20, 24] -> [76, 80]
        # Transformed Y: +x + 50 for x in [10, 12] -> [60, 62]
        # Transformed Z: [5, 6]
        link_xform = MockRevitTransform(
            bx=(0.0, 1.0, 0.0),
            by=(-1.0, 0.0, 0.0),
            bz=(0.0, 0.0, 1.0),
            origin=(100.0, 50.0, 0.0),
        )

        raw_bbox = MockBBoxXYZ((0.0, 0.0, 0.0), (2.0, 4.0, 1.0), transform=bbox_xform)
        elem = MockElemWithBBox(raw_bbox)
        link = MockLinkWithTransform(link_xform)

        host_bbox, diags = extract_element_host_bbox(elem, link_instance=link)
        self.assertIsNotNone(host_bbox)
        self.assertEqual(len(diags), 0)
        self.assertTrue(host_bbox.min_pt.is_almost_equal(Vector3(76.0, 60.0, 5.0)))
        self.assertTrue(host_bbox.max_pt.is_almost_equal(Vector3(80.0, 62.0, 6.0)))

        # Element without BoundingBoxXYZ returns (None, diagnostics)
        no_bbox, no_diags = extract_element_host_bbox(MockElemWithBBox(None))
        self.assertIsNone(no_bbox)
        self.assertTrue(len(no_diags) > 0)

    def test_calculate_clash_framing_modes_and_safeguards(self):
        class MockElem(object):
            def __init__(self, min_t, max_t):
                self._min = Vector3(*min_t)
                self._max = Vector3(*max_t)

            def get_BoundingBox(self, view):
                class _B(object):
                    pass
                b = _B()
                b.Min = self._min
                b.Max = self._max
                b.Transform = None
                return b

        elem_host_1 = MockElem((10.0, 10.0, 5.0), (11.0, 11.0, 6.0))
        elem_host_2 = MockElem((10.5, 10.5, 5.5), (12.0, 12.0, 7.0))
        elem_link_2 = MockElem((10.5, 10.5, 5.5), (12.0, 12.0, 7.0))

        res_host_1 = ParticipantResolution(
            participant=ClashParticipant(model_name="Host", element_id=1),
            status=ResolutionStatus.RESOLVED,
            target_type=TargetDocumentType.HOST,
            host_element=elem_host_1,
        )
        res_host_2 = ParticipantResolution(
            participant=ClashParticipant(model_name="Host", element_id=2),
            status=ResolutionStatus.RESOLVED,
            target_type=TargetDocumentType.HOST,
            host_element=elem_host_2,
        )
        res_link_2 = ParticipantResolution(
            participant=ClashParticipant(model_name="Link", element_id=3),
            status=ResolutionStatus.RESOLVED,
            target_type=TargetDocumentType.LINK,
            linked_element=elem_link_2,
        )
        res_unresolved = ParticipantResolution(
            participant=ClashParticipant(model_name="Missing", element_id=999),
            status=ResolutionStatus.UNRESOLVED,
            target_type=TargetDocumentType.UNKNOWN,
        )

        # 1. Mode A ('pair') Host vs Host -> NATIVE_ELEMENT_ISOLATION + minimum span enforcement (4.0 ft)
        plan_pair_host = calculate_clash_framing(
            res_host_1,
            res_host_2,
            mode="pair",
            pair_margin_ft=0.5,
            min_box_span_ft=4.0,
        )
        self.assertTrue(plan_pair_host.is_valid)
        self.assertEqual(plan_pair_host.framing_method, FramingMethod.AUTO_FRAMED_PARTICIPANTS)
        self.assertEqual(plan_pair_host.isolation_method, IsolationMethod.NATIVE_ELEMENT_ISOLATION)
        # Union is [10, 12] (span 2.0) + 2*0.5 margin = span 3.0 -> enforced to min_box_span_ft = 4.0
        self.assertTrue(plan_pair_host.section_box.size.is_almost_equal(Vector3(4.0, 4.0, 4.0)))
        self.assertTrue(plan_pair_host.section_box.center.is_almost_equal(Vector3(11.0, 11.0, 6.0)))

        # 2. Mode A ('pair') Host vs Link -> LINK_CATEGORY_AND_SECTION_BOX
        plan_pair_link = calculate_clash_framing(
            res_host_1,
            res_link_2,
            mode="pair",
            pair_margin_ft=0.5,
        )
        self.assertTrue(plan_pair_link.is_valid)
        self.assertEqual(plan_pair_link.isolation_method, IsolationMethod.LINK_CATEGORY_AND_SECTION_BOX)
        self.assertTrue(any("LINK_CATEGORY_AND_SECTION_BOX" in d for d in plan_pair_link.diagnostics))

        # 3. Mode B ('context') -> CONTEXT_SECTION_BOX_ONLY with context_margin_ft=3.0
        plan_context = calculate_clash_framing(
            res_host_1,
            res_link_2,
            mode="context",
            context_margin_ft=3.0,
            min_box_span_ft=4.0,
        )
        self.assertTrue(plan_context.is_valid)
        self.assertEqual(plan_context.isolation_method, IsolationMethod.CONTEXT_SECTION_BOX_ONLY)
        # Union [10, 12] (span 2.0) + 2*3.0 margin = span 8.0
        self.assertTrue(plan_context.section_box.size.is_almost_equal(Vector3(8.0, 8.0, 8.0)))

        # 4. Single-participant fallback when Participant B is unresolved
        plan_single = calculate_clash_framing(
            res_host_1,
            res_unresolved,
            mode="pair",
        )
        self.assertTrue(plan_single.is_valid)
        self.assertEqual(plan_single.framing_method, FramingMethod.SINGLE_PARTICIPANT_FALLBACK)
        self.assertTrue(any("Single-participant fallback" in d for d in plan_single.diagnostics))

        # 5. Both missing geometry -> UNAVAILABLE_GEOMETRY_FALLBACK and IsolationMethod.NONE
        plan_none = calculate_clash_framing(
            res_unresolved,
            res_unresolved,
            mode="pair",
        )
        self.assertFalse(plan_none.is_valid)
        self.assertEqual(plan_none.framing_method, FramingMethod.UNAVAILABLE_GEOMETRY_FALLBACK)
        self.assertEqual(plan_none.isolation_method, IsolationMethod.NONE)
        self.assertIsNone(plan_none.section_box)

        # 6. Viewpoint with UNVERIFIED_COORDINATE_FRAME MUST NOT claim EXACT_CAMERA_SYNC
        vp = CameraViewpoint(
            name="VP1",
            position=ClashPoint(20.0, 20.0, 10.0, units="ft"),
            quaternion=(0.0, 0.0, 0.0, 1.0),
            forward_vector=(0.0, 0.0, -1.0),
            up_vector=(0.0, 1.0, 0.0),
            units="ft",
            units_verified=True,
        )
        plan_unverified_vp = calculate_clash_framing(
            res_host_1,
            res_host_2,
            mode="pair",
            viewpoint=vp,
            coord_frame_status=CoordinateFrameStatus.UNVERIFIED_COORDINATE_FRAME,
        )
        self.assertEqual(plan_unverified_vp.framing_method, FramingMethod.AUTO_FRAMED_PARTICIPANTS)
        self.assertNotEqual(plan_unverified_vp.framing_method, FramingMethod.EXACT_CAMERA_SYNC)
        self.assertTrue(any("not used for EXACT_CAMERA_SYNC" in d for d in plan_unverified_vp.diagnostics))

        # 7. Viewpoint with VERIFIED_INTERNAL and units_verified=True upgrades to EXACT_CAMERA_SYNC
        plan_verified_vp = calculate_clash_framing(
            res_host_1,
            res_host_2,
            mode="pair",
            viewpoint=vp,
            coord_frame_status=CoordinateFrameStatus.VERIFIED_INTERNAL,
        )
        self.assertEqual(plan_verified_vp.framing_method, FramingMethod.EXACT_CAMERA_SYNC)
        self.assertTrue(plan_verified_vp.eye_position.is_almost_equal(Vector3(20.0, 20.0, 10.0)))

        # 8. Corroboration helper is strictly diagnostic
        corrob = evaluate_clash_geometry_corroboration(
            plan_pair_host.bbox_a,
            plan_pair_host.bbox_b,
            clash_point_ft=Vector3(10.8, 10.8, 5.8),
        )
        self.assertTrue(corrob["is_corroborating_only"])
        self.assertTrue(corrob["bboxes_intersect"])
        self.assertTrue(corrob["clash_point_in_union"])

    def test_continuous_element_framing_protection(self):
        """When an element is a massive continuous slab (250 ft), framing focuses on compact element."""
        # Participant A: compact ceiling panel [10, 13] x [20, 23] x [9, 10] (span = 3.0 ft)
        box_a = BoundingBox3D(Vector3(10.0, 20.0, 9.0), Vector3(13.0, 23.0, 10.0))
        # Participant B: massive floor slab [0, 250] x [0, 180] x [8, 10] (span = 250.0 ft)
        box_b = BoundingBox3D(Vector3(0.0, 0.0, 8.0), Vector3(250.0, 180.0, 10.0))

        plan = calculate_clash_framing(
            bbox_a=box_a,
            bbox_b=box_b,
            mode="pair",
            pair_margin_ft=1.0,
            min_box_span_ft=5.0,
            max_box_span_ft=16.0,
        )
        self.assertTrue(plan.is_valid)
        # Verify the section box is tightly cropped on the compact element and NOT 250 ft!
        max_dim = max(plan.section_box.size.x, plan.section_box.size.y, plan.section_box.size.z)
        self.assertLessEqual(max_dim, 16.0)
        self.assertGreaterEqual(max_dim, 5.0)
        # Verify center of section box is close to Participant A's center (11.5, 21.5, 9.5)
        self.assertAlmostEqual(plan.section_box.center.x, 11.5, delta=1.0)
        self.assertAlmostEqual(plan.section_box.center.y, 21.5, delta=1.0)
        self.assertTrue(any("Focused collision framing" in d or "continuous element" in d for d in plan.diagnostics))

    def test_isolated_mode_encompasses_full_elements_without_cropping(self):
        """In isolated mode, standard elements (e.g. 20 ft beam vs 15 ft ceiling) show fully."""
        # Participant A: 15 ft ceiling [0, 15] x [0, 8] x [9, 9.5]
        box_a = BoundingBox3D(Vector3(0.0, 0.0, 9.0), Vector3(15.0, 8.0, 9.5))
        # Participant B: 20 ft beam [5, 25] x [3, 4] x [8.5, 11]
        box_b = BoundingBox3D(Vector3(5.0, 3.0, 8.5), Vector3(25.0, 4.0, 11.0))

        plan = calculate_clash_framing(
            bbox_a=box_a,
            bbox_b=box_b,
            mode="pair",
            pair_margin_ft=1.0,
            min_box_span_ft=4.0,
            max_box_span_ft=80.0,
        )
        self.assertTrue(plan.is_valid)
        # Full union of A and B is [0, 25] x [0, 8] x [8.5, 11]
        # With 1.0 margin: [-1.0, 26.0] x [-1.0, 9.0] x [7.5, 12.0]
        sb = plan.section_box
        self.assertLessEqual(sb.min_pt.x, 0.0)
        self.assertGreaterEqual(sb.max_pt.x, 25.0)
        self.assertLessEqual(sb.min_pt.y, 0.0)
        self.assertGreaterEqual(sb.max_pt.y, 8.0)
        self.assertLessEqual(sb.min_pt.z, 8.5)
        self.assertGreaterEqual(sb.max_pt.z, 11.0)
        # Verify both elements are 100% inside the section box (NOT cropped into!)
        self.assertTrue(sb.contains_point(box_a.min_pt))
        self.assertTrue(sb.contains_point(box_a.max_pt))
        self.assertTrue(sb.contains_point(box_b.min_pt))
        self.assertTrue(sb.contains_point(box_b.max_pt))


if __name__ == "__main__":
    unittest.main()
