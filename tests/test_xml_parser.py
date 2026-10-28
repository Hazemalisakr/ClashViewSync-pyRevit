# -*- coding: utf-8 -*-
"""Unit tests for clashview_sync.xml_parser."""
from __future__ import absolute_import, division, print_function

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

from clashview_sync.coordinate_transformer import convert_length
from clashview_sync.xml_parser import (
    merge_clash_and_viewpoints,
    parse_clash_xml,
    parse_viewpoints_xml,
)

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class TestXmlParser(unittest.TestCase):
    """Tests XML parsing, nesting, unit detection, and viewpoint merging."""

    def test_standard_clash_xml(self):
        xml_path = os.path.join(_FIXTURES_DIR, "standard_clash.xml")
        payload = parse_clash_xml(xml_path)

        self.assertEqual(payload.units, "m")
        self.assertEqual(len(payload.tests), 1)

        test = payload.tests[0]
        self.assertEqual(test.name, "MEP vs Architecture")
        self.assertEqual(test.test_type, "hard")
        self.assertEqual(test.status, "Active")
        self.assertAlmostEqual(test.tolerance, 0.010, places=5)
        self.assertEqual(len(test.results), 1)

        clash = test.results[0]
        self.assertEqual(clash.name, "Clash1")
        self.assertEqual(clash.guid, "c001-guid-test")
        self.assertEqual(clash.status, "Active")
        self.assertAlmostEqual(clash.distance, -0.025, places=5)
        self.assertIn("Hard clash", clash.description)

        # Clash point
        self.assertIsNotNone(clash.clash_point)
        self.assertAlmostEqual(clash.clash_point.x, 10.500, places=3)
        self.assertAlmostEqual(clash.clash_point.y, 20.000, places=3)
        self.assertAlmostEqual(clash.clash_point.z, 3.500, places=3)
        self.assertEqual(clash.clash_point.units, "m")

        # Participant A
        p_a = clash.participant_a
        self.assertIsNotNone(p_a)
        self.assertEqual(p_a.model_name, "MEP_Services.nwc")
        self.assertEqual(p_a.element_id, 1023456)
        self.assertEqual(p_a.element_id_raw, "1023456")
        self.assertTrue(p_a.is_valid_element_id)
        self.assertEqual(p_a.category, "Ducts")
        self.assertEqual(p_a.item_name, "Rectangular Duct 300x200")
        self.assertEqual(p_a.layer, "Level 1")
        self.assertEqual(len(p_a.path_nodes), 4)

        # Participant B
        p_b = clash.participant_b
        self.assertIsNotNone(p_b)
        self.assertEqual(p_b.model_name, "Arch_Core.nwc")
        self.assertEqual(p_b.element_id, 2054321)
        self.assertEqual(p_b.element_id_raw, "2054321")
        self.assertTrue(p_b.is_valid_element_id)
        self.assertEqual(p_b.category, "Walls")
        self.assertEqual(p_b.item_name, "Generic - 200mm")

        self.assertTrue(clash.has_both_element_ids)

        # Comments
        self.assertEqual(len(clash.comments), 1)
        self.assertEqual(clash.comments[0]["user"], "Coordinator")
        self.assertIn("Needs sleeve", clash.comments[0]["body"])

    def test_nested_groups_clash_xml(self):
        xml_path = os.path.join(_FIXTURES_DIR, "nested_groups_clash.xml")
        payload = parse_clash_xml(xml_path)

        self.assertEqual(len(payload.tests), 1)
        test = payload.tests[0]

        # 1 group, 3 flat results total (2 inside group, 1 standalone)
        self.assertEqual(len(test.groups), 1)
        self.assertEqual(len(test.results), 3)

        grp = test.groups[0]
        self.assertEqual(grp.name, "Group_Level2_Pipes")
        self.assertEqual(grp.guid, "grp-guid-001")
        self.assertEqual(grp.status, "Reviewed")
        self.assertEqual(grp.count, 2)
        self.assertIsNotNone(grp.clash_point)

        # Check grouped results have parent_group populated
        res_pipe_1 = test.results[0]
        self.assertEqual(res_pipe_1.name, "Clash_Pipe_1")
        self.assertEqual(res_pipe_1.parent_group, "Group_Level2_Pipes")
        self.assertEqual(res_pipe_1.parent_group_guid, "grp-guid-001")
        self.assertTrue(res_pipe_1.is_grouped)
        self.assertEqual(res_pipe_1.participant_a.element_id, 301)
        self.assertEqual(res_pipe_1.participant_b.element_id, 401)

        res_pipe_2 = test.results[1]
        self.assertEqual(res_pipe_2.name, "Clash_Pipe_2")
        self.assertEqual(res_pipe_2.parent_group, "Group_Level2_Pipes")
        self.assertTrue(res_pipe_2.is_grouped)

        # Check standalone result
        res_standalone = test.results[2]
        self.assertEqual(res_standalone.name, "Standalone_Clash_3")
        self.assertIsNone(res_standalone.parent_group)
        self.assertFalse(res_standalone.is_grouped)

    def test_missing_ids_and_optionals_xml(self):
        xml_path = os.path.join(_FIXTURES_DIR, "missing_ids_and_optionals.xml")
        payload = parse_clash_xml(xml_path)

        self.assertEqual(len(payload.tests), 1)
        test = payload.tests[0]
        self.assertEqual(len(test.results), 3)

        # Result 1: Non-numeric ID and missing ID
        r1 = test.results[0]
        self.assertEqual(r1.name, "Missing_Clashpoint_Clash")
        self.assertIsNone(r1.clash_point)  # Missing point
        self.assertTrue(any("clashpoint" in d.lower() for d in r1.diagnostics))

        # Item 1 has non-numeric ID
        p1_a = r1.participant_a
        self.assertIsNone(p1_a.element_id)
        self.assertEqual(p1_a.element_id_raw, "NOT_A_NUMBER")
        self.assertFalse(p1_a.is_valid_element_id)
        self.assertTrue(len(p1_a.diagnostics) > 0)

        # Item 2 has missing Element ID - verify parser does NOT guess from Material or GUID
        p1_b = r1.participant_b
        self.assertIsNone(p1_b.element_id)
        self.assertIsNone(p1_b.element_id_raw)
        self.assertFalse(p1_b.is_valid_element_id)
        self.assertEqual(p1_b.attributes.get("Material"), "Steel")
        self.assertEqual(p1_b.attributes.get("GUID"), "{ABCDEF-1234}")

        self.assertFalse(r1.has_both_element_ids)

        # Result 2: Only 1 participant
        r2 = test.results[1]
        self.assertIsNotNone(r2.participant_a)
        self.assertIsNone(r2.participant_b)
        self.assertTrue(any("1 participant" in d for d in r2.diagnostics))

        # Result 3: 0 participants
        r3 = test.results[2]
        self.assertIsNone(r3.participant_a)
        self.assertIsNone(r3.participant_b)
        self.assertTrue(any("0 participants" in d for d in r3.diagnostics))

    def test_units_millimeter_xml(self):
        xml_path = os.path.join(_FIXTURES_DIR, "units_millimeter.xml")
        payload = parse_clash_xml(xml_path)

        self.assertEqual(payload.units, "mm")
        test = payload.tests[0]
        clash = test.results[0]
        self.assertEqual(clash.clash_point.units, "mm")
        self.assertAlmostEqual(clash.clash_point.x, 10000.0, places=1)
        self.assertAlmostEqual(clash.clash_point.y, 20000.0, places=1)
        self.assertAlmostEqual(clash.clash_point.z, 3000.0, places=1)

        # Convert to Revit internal feet
        x_ft = convert_length(clash.clash_point.x, "mm", "ft")
        expected_x_ft = 10000.0 * 0.001 / 0.3048
        self.assertAlmostEqual(x_ft, expected_x_ft, places=5)

    def test_saved_viewpoints_xml(self):
        vp_path = os.path.join(_FIXTURES_DIR, "saved_viewpoints.xml")
        payload = parse_viewpoints_xml(vp_path)

        self.assertEqual(payload.source_format, "viewpoints_xml")
        self.assertEqual(len(payload.viewpoints), 4)

        # Find Clash1 view
        vps = [v for v in payload.viewpoints if v.name == "Clash1"]
        self.assertEqual(len(vps), 1)
        vp1 = vps[0]
        self.assertEqual(vp1.guid, "c001-guid-test")
        self.assertEqual(vp1.projection, "persp")
        self.assertAlmostEqual(vp1.focal_distance, 15.0, places=3)
        self.assertAlmostEqual(vp1.fov, 45.0, places=3)
        self.assertIsNotNone(vp1.position)
        self.assertEqual(vp1.position.to_tuple(), (15.0, 25.0, 8.0))
        self.assertIsNotNone(vp1.quaternion)
        self.assertAlmostEqual(vp1.quaternion[2], 0.3826834, places=6)
        self.assertAlmostEqual(vp1.quaternion[3], 0.9238795, places=6)

        # Derived camera vectors must be non-empty
        self.assertIsNotNone(vp1.forward_vector)
        self.assertIsNotNone(vp1.up_vector)

        # Ortho view
        ortho_vps = [v for v in payload.viewpoints if v.name == "Ortho_View_Test"]
        self.assertEqual(len(ortho_vps), 1)
        vp_ortho = ortho_vps[0]
        self.assertEqual(vp_ortho.projection, "ortho")
        self.assertAlmostEqual(vp_ortho.view_height, 12.0, places=3)
        self.assertAlmostEqual(vp_ortho.aspect, 1.5, places=3)

    def test_merge_clash_and_viewpoints_unambiguous(self):
        clash_path = os.path.join(_FIXTURES_DIR, "standard_clash.xml")
        vp_path = os.path.join(_FIXTURES_DIR, "saved_viewpoints.xml")

        clash_payload = parse_clash_xml(clash_path)
        vp_payload = parse_viewpoints_xml(vp_path)

        # Before merge
        self.assertIsNone(clash_payload.tests[0].results[0].viewpoint)

        # Merge
        merged = merge_clash_and_viewpoints(clash_payload, vp_payload)
        clash = merged.tests[0].results[0]

        # Must be matched unambiguously
        self.assertIsNotNone(clash.viewpoint)
        self.assertEqual(clash.viewpoint.name, "Clash1")
        self.assertEqual(clash.viewpoint.guid, "c001-guid-test")

    def test_merge_clash_and_viewpoints_ambiguity_safety(self):
        # Create a clash result named "Ambiguous_Clash" without matching GUID
        clash_snippet = """<exchange units="m"><batchtest name="B1"><clashtests>
          <clashtest name="T1"><clashresults>
            <clashresult name="Ambiguous_Clash" guid="no-guid-match">
              <clashpoint><pos3f x="0" y="0" z="0"/></clashpoint>
            </clashresult>
          </clashresults></clashtest>
        </clashtests></batchtest></exchange>"""

        clash_payload = parse_clash_xml(clash_snippet)
        vp_path = os.path.join(_FIXTURES_DIR, "saved_viewpoints.xml")
        vp_payload = parse_viewpoints_xml(vp_path)

        # In saved_viewpoints.xml there are two views named "Ambiguous_Clash"
        merged = merge_clash_and_viewpoints(clash_payload, vp_payload)
        clash = merged.tests[0].results[0]

        # MUST NOT silently pair one!
        self.assertIsNone(clash.viewpoint)

        # MUST record ambiguity diagnostic
        has_amb_diag = any("Ambiguous viewpoint match" in d for d in clash.diagnostics)
        self.assertTrue(has_amb_diag)

    def test_malformed_xml_handling(self):
        malformed_path = os.path.join(_FIXTURES_DIR, "malformed.xml")
        # Must not raise unhandled ParseError
        payload = parse_clash_xml(malformed_path)
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload.tests), 0)
        self.assertTrue(len(payload.diagnostics) > 0)
        self.assertTrue(any("ParseError" in d for d in payload.diagnostics))

    def test_non_ascii_utf8_and_bom_xml_parsing(self):
        # Test Unicode XML string with Arabic and accented characters + BOM
        xml_unicode = (
            u'\ufeff<?xml version="1.0" encoding="UTF-8"?>'
            u'<exchange units="m"><batchtest name="Batch_B\u00f8">'
            u'<clashtests><clashtest name="MEP_\u062d\u0627\u0632\u0645_Caf\u00e9">'
            u'<clashresults><clashresult name="Clash_\u00f81" guid="g-utf8-1" status="Active" distance="-0.015">'
            u'<clashpoint><pos3f x="1.0" y="2.0" z="3.0"/></clashpoint>'
            u'<clashobjects>'
            u'<clashobject><pathlink><node>Mod\u00e8le_\u0645\u0639\u0645\u0627\u0631\u064a.nwc</node></pathlink>'
            u'<smarttags><smarttag><name>Item Name</name><value>Mur_B\u00e9ton_\u062c\u062f\u0627\u0631</value></smarttag></smarttags>'
            u'<objectattribute><name>Element ID</name><value>777888</value></objectattribute>'
            u'</clashobject>'
            u'<clashobject><pathlink><node>MEP_Caf\u00e9.nwc</node></pathlink>'
            u'<objectattribute><name>Element ID</name><value>888999</value></objectattribute>'
            u'</clashobject>'
            u'</clashobjects>'
            u'</clashresult></clashresults></clashtest></clashtests></batchtest></exchange>'
        )
        payload = parse_clash_xml(xml_unicode)
        self.assertEqual(len(payload.tests), 1)
        self.assertTrue(payload.units_verified)
        test = payload.tests[0]
        self.assertEqual(test.name, u"MEP_\u062d\u0627\u0632\u0645_Caf\u00e9")
        clash = test.results[0]
        self.assertEqual(clash.name, u"Clash_\u00f81")
        self.assertEqual(
            clash.participant_a.model_name,
            u"Mod\u00e8le_\u0645\u0639\u0645\u0627\u0631\u064a.nwc",
        )
        self.assertEqual(
            clash.participant_a.item_name,
            u"Mur_B\u00e9ton_\u062c\u062f\u0627\u0631",
        )
        self.assertEqual(clash.participant_a.element_id, 777888)

        # Also test already UTF-8 encoded byte sequence
        xml_utf8_bytes = xml_unicode.encode("utf-8")
        payload_bytes = parse_clash_xml(xml_utf8_bytes)
        self.assertEqual(len(payload_bytes.tests), 1)
        self.assertEqual(
            payload_bytes.tests[0].name,
            u"MEP_\u062d\u0627\u0632\u0645_Caf\u00e9",
        )
        self.assertEqual(
            payload_bytes.tests[0].results[0].participant_a.item_name,
            u"Mur_B\u00e9ton_\u062c\u062f\u0627\u0631",
        )

    def test_viewpoints_missing_units_diagnostic_and_unverified_flag(self):
        # Viewpoints XML with NO units attribute on <exchange> or <viewpoints>
        vp_no_units = """<?xml version="1.0" encoding="UTF-8"?>
        <exchange>
          <viewpoints>
            <view name="Unverified_VP" guid="vp-no-unit-1">
              <viewpoint focal="10.0" fov="45.0">
                <camera projection="persp">
                  <position><pos3f x="5.0" y="6.0" z="7.0"/></position>
                  <rotation><quaternion a="0.0" b="0.0" c="0.0" d="1.0"/></rotation>
                </camera>
              </viewpoint>
            </view>
          </viewpoints>
        </exchange>"""

        payload = parse_viewpoints_xml(vp_no_units)
        self.assertEqual(payload.units, "m")
        self.assertFalse(payload.units_verified)
        self.assertEqual(len(payload.viewpoints), 1)
        self.assertFalse(payload.viewpoints[0].units_verified)
        self.assertTrue(
            any("No explicit units declared in viewpoints XML" in d for d in payload.diagnostics)
        )


if __name__ == "__main__":
    unittest.main()
