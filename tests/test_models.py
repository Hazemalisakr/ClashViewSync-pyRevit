# -*- coding: utf-8 -*-
"""Unit tests for clashview_sync.models."""
from __future__ import absolute_import, division, print_function

import os
import sys
import unittest

# Ensure extension lib is importable
_LIB_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        os.path.join(os.path.dirname(__file__), "..", "lib"),
    )
)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from clashview_sync.models import (
    CameraViewpoint,
    ClashGroup,
    ClashParticipant,
    ClashPoint,
    ClashResultItem,
    ClashSyncPayload,
    ClashTestGroup,
)


class TestModels(unittest.TestCase):
    """Tests data structures and model behaviors."""

    def test_clash_point(self):
        pt = ClashPoint(1.0, 2.0, 3.5, units="m")
        self.assertEqual(pt.to_tuple(), (1.0, 2.0, 3.5))
        self.assertEqual(pt.units, "m")
        d = pt.to_dict()
        self.assertEqual(d["x"], 1.0)
        self.assertEqual(d["y"], 2.0)
        self.assertEqual(d["z"], 3.5)
        self.assertEqual(d["units"], "m")
        self.assertIn("ClashPoint", repr(pt))

        pt2 = ClashPoint(1.0, 2.0, 3.5, units="m")
        self.assertEqual(pt, pt2)

    def test_clash_participant_valid_id(self):
        p = ClashParticipant(
            model_name="Arch.nwc",
            element_id=123456,
            element_id_raw="123456",
            item_name="Wall 200mm",
            category="Walls",
            layer="Level 1",
            path_nodes=["Arch.nwc", "Walls", "Wall 200mm"],
            smarttags={"Item Name": "Wall 200mm"},
            attributes={"Element ID": "123456"},
        )
        self.assertEqual(p.model_name, "Arch.nwc")
        self.assertEqual(p.element_id, 123456)
        self.assertEqual(p.element_id_raw, "123456")
        self.assertTrue(p.is_valid_element_id)
        self.assertEqual(p.category, "Walls")
        d = p.to_dict()
        self.assertTrue(d["is_valid_element_id"])
        self.assertEqual(d["element_id"], 123456)
        self.assertIn("Arch.nwc", repr(p))

    def test_clash_participant_invalid_or_missing_id(self):
        p_invalid = ClashParticipant(
            model_name="Pipes.nwc",
            element_id=None,
            element_id_raw="NOT_A_NUM",
            diagnostics=["Invalid Element ID"],
        )
        self.assertIsNone(p_invalid.element_id)
        self.assertEqual(p_invalid.element_id_raw, "NOT_A_NUM")
        self.assertFalse(p_invalid.is_valid_element_id)
        self.assertEqual(len(p_invalid.diagnostics), 1)

        p_negative = ClashParticipant(element_id=-1, element_id_raw="-1")
        self.assertFalse(p_negative.is_valid_element_id)

    def test_camera_viewpoint(self):
        vp = CameraViewpoint(
            name="Clash_View_1",
            guid="guid-123",
            projection="persp",
            position=(10.0, 20.0, 5.0),
            quaternion=(0.0, 0.0, 0.0, 1.0),
            focal_distance=15.0,
            fov=45.0,
            aspect=1.333,
            view_height=10.0,
            units="m",
        )
        self.assertEqual(vp.name, "Clash_View_1")
        self.assertEqual(vp.projection, "persp")
        self.assertIsInstance(vp.position, ClashPoint)
        self.assertEqual(vp.position.to_tuple(), (10.0, 20.0, 5.0))
        self.assertEqual(vp.quaternion, (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(vp.focal_distance, 15.0)
        self.assertEqual(vp.fov, 45.0)

        # Test orthographic normalization
        vp_ortho = CameraViewpoint(projection="orthographic")
        self.assertEqual(vp_ortho.projection, "ortho")

    def test_clash_result_item(self):
        pt = ClashPoint(10.0, 20.0, 3.0)
        p1 = ClashParticipant(model_name="M1.nwc", element_id=100)
        p2 = ClashParticipant(model_name="M2.nwc", element_id=200)

        item = ClashResultItem(
            name="Clash1",
            guid="guid-c1",
            status="Active",
            distance=-0.05,
            clash_point=pt,
            participant_a=p1,
            participant_b=p2,
        )
        self.assertEqual(item.name, "Clash1")
        self.assertEqual(item.distance, -0.05)
        self.assertTrue(item.has_both_element_ids)
        self.assertFalse(item.is_grouped)
        self.assertEqual(len(item.all_participants), 2)

        # Test grouped
        item_grouped = ClashResultItem(
            name="Clash2",
            parent_group="Group_1",
            participant_a=p1,
            participant_b=ClashParticipant(element_id=None),
        )
        self.assertTrue(item_grouped.is_grouped)
        self.assertFalse(item_grouped.has_both_element_ids)

    def test_clash_group_and_test(self):
        r1 = ClashResultItem(name="C1", parent_group="G1")
        r2 = ClashResultItem(name="C2", parent_group="G1")
        r3 = ClashResultItem(name="C3")

        grp = ClashGroup(name="G1", results=[r1, r2])
        self.assertEqual(grp.count, 2)
        self.assertIn("G1", repr(grp))

        test = ClashTestGroup(
            name="Test1",
            test_type="hard",
            tolerance=0.01,
            results=[r1, r2, r3],
            groups=[grp],
        )
        self.assertEqual(test.total_results_count, 3)
        self.assertEqual(len(test.groups), 1)

    def test_clash_sync_payload(self):
        r1 = ClashResultItem(name="C1")
        t1 = ClashTestGroup(name="T1", results=[r1])
        payload = ClashSyncPayload(
            source_file="test.xml",
            source_format="clash_xml",
            units="m",
            tests=[t1],
        )
        self.assertEqual(payload.total_clashes_count, 1)
        summary = payload.summary()
        self.assertEqual(summary["test_count"], 1)
        self.assertEqual(summary["clash_count"], 1)
        self.assertEqual(summary["units"], "m")


if __name__ == "__main__":
    unittest.main()
