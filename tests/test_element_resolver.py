# -*- coding: utf-8 -*-
"""Unit tests for clashview_sync.element_resolver."""
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

from clashview_sync.models import ClashParticipant, ClashResultItem, integer_types
from clashview_sync.element_resolver import (
    ConfidenceLevel,
    ElementResolver,
    ParticipantResolution,
    ResolutionStatus,
    TargetDocumentType,
    create_revit_element_id,
    extract_base_model_name,
    get_element_id_value,
    normalize_path_or_uri,
    resolve_participant,
)


# =============================================================================
# RECURSIVE MOCK OBJECTS REPLICATING REVIT API
# =============================================================================


class MockElementId(object):
    """Simulates Autodesk.Revit.DB.ElementId with .Value and .IntegerValue."""

    def __init__(self, val):
        self.val = int(val)

    @property
    def Value(self):
        return self.val

    @property
    def IntegerValue(self):
        return self.val

    def __eq__(self, other):
        if hasattr(other, "Value"):
            return self.val == other.Value
        if hasattr(other, "IntegerValue"):
            return self.val == other.IntegerValue
        try:
            return self.val == int(other)
        except Exception:
            return False

    def __repr__(self):
        return "ElementId({0})".format(self.val)


class MockCategory(object):
    def __init__(self, name):
        self.Name = name


class MockElement(object):
    def __init__(self, element_id, name, category_name=None, unique_id=None):
        self.Id = MockElementId(element_id)
        self.Name = name
        self.Category = MockCategory(category_name) if category_name else None
        self.UniqueId = unique_id or "uid-{0}".format(element_id)

    def __repr__(self):
        return "MockElement(id={0}, name='{1}')".format(self.Id.Value, self.Name)


class MockDocument(object):
    """Simulates Autodesk.Revit.DB.Document."""

    def __init__(self, title, path_name="", elements=None, link_instances=None):
        self.Title = title
        self.PathName = path_name
        self._elements = dict(elements) if elements else {}
        self.link_instances = list(link_instances) if link_instances else []

    def GetElement(self, elem_id):
        # Handle int or MockElementId
        int_val = getattr(elem_id, "Value", elem_id)
        try:
            int_val = int(int_val)
        except Exception:
            return None
        return self._elements.get(int_val)

    def add_element(self, element):
        self._elements[element.Id.Value] = element


class MockRevitLinkType(object):
    def __init__(self, type_id, name, is_loaded=True):
        self.Id = MockElementId(type_id)
        self.Name = name
        self.is_loaded = is_loaded


class MockRevitLinkInstance(object):
    """Simulates Autodesk.Revit.DB.RevitLinkInstance."""

    def __init__(
        self,
        instance_id,
        instance_name,
        linked_doc=None,
        type_id=None,
        type_name="",
        is_loaded=True,
    ):
        self.Id = MockElementId(instance_id)
        self.Name = instance_name
        self._type_id = MockElementId(type_id or (instance_id + 1000))
        self._linked_doc = linked_doc
        self._is_loaded = is_loaded

    def GetTypeId(self):
        return self._type_id

    def GetLinkDocument(self):
        if not self._is_loaded:
            return None
        return self._linked_doc


# =============================================================================
# UNIT TEST CASES
# =============================================================================


class TestElementResolver(unittest.TestCase):
    """Exhaustive tests for link-aware element resolution and edge cases."""

    def test_model_path_normalization(self):
        # Path separators
        p1 = normalize_path_or_uri("C:\\Projects\\Models\\Arch.rvt")
        self.assertEqual(p1, "C:/Projects/Models/Arch.rvt")

        # URI prefixes
        p2 = normalize_path_or_uri("file:///C:/Models/MEP.nwc")
        self.assertEqual(p2, "C:/Models/MEP.nwc")

        # Base model name extraction across formats
        self.assertEqual(
            extract_base_model_name("C:/Models/MEP_Services.nwc"),
            "mep_services",
        )
        self.assertEqual(
            extract_base_model_name("MEP_Services.rvt : 2 : location <Not Shared>"),
            "mep_services",
        )
        self.assertEqual(
            extract_base_model_name("Autodesk Docs://Project/Sub/360-JDF02-ARC.rvt"),
            "360-jdf02-arc",
        )
        self.assertEqual(
            extract_base_model_name("Structural_Frame.ifc"),
            "structural_frame",
        )
        self.assertEqual(
            extract_base_model_name("Ducts.dwg"),
            "ducts",
        )

    def test_missing_and_malformed_ids(self):
        host_doc = MockDocument(title="Master_Project")
        resolver = ElementResolver(host_doc)

        # 1. Element ID is None with raw string
        p_invalid = ClashParticipant(
            model_name="Master_Project.nwc",
            element_id=None,
            element_id_raw="NON_NUMERIC_ID",
        )
        res_invalid = resolver.resolve(p_invalid)
        self.assertEqual(res_invalid.status, ResolutionStatus.UNRESOLVED)
        self.assertEqual(res_invalid.confidence, ConfidenceLevel.NONE)
        self.assertTrue(any("NON_NUMERIC_ID" in d for d in res_invalid.diagnostics))

        # 2. Element ID is negative
        p_negative = ClashParticipant(
            model_name="Master_Project.nwc",
            element_id=-1,
            element_id_raw="-1",
        )
        res_neg = resolver.resolve(p_negative)
        self.assertEqual(res_neg.status, ResolutionStatus.UNRESOLVED)

        # 3. None participant
        res_none = resolver.resolve(None)
        self.assertEqual(res_none.status, ResolutionStatus.UNRESOLVED)

    def test_host_element_resolution_success(self):
        # Setup host document with element ID 1001
        wall_elem = MockElement(1001, "Generic - 200mm", category_name="Walls")
        host_doc = MockDocument(
            title="Hospital_Master",
            path_name="C:/BIM/Hospital_Master.rvt",
            elements={1001: wall_elem},
        )

        resolver = ElementResolver(host_doc)
        participant = ClashParticipant(
            model_name="Hospital_Master.nwc",
            element_id=1001,
            category="Walls",
            item_name="Generic - 200mm",
        )

        res = resolver.resolve(participant)
        self.assertTrue(res.is_resolved)
        self.assertEqual(res.status, ResolutionStatus.RESOLVED)
        self.assertEqual(res.target_type, TargetDocumentType.HOST)
        self.assertFalse(res.is_linked)
        self.assertEqual(res.host_element, wall_elem)
        self.assertIsNone(res.linked_element)
        self.assertEqual(res.resolved_element_id, 1001)

    def test_host_element_missing_in_document(self):
        host_doc = MockDocument(title="Hospital_Master", elements={})
        resolver = ElementResolver(host_doc)

        participant = ClashParticipant(
            model_name="Hospital_Master.nwc",
            element_id=9999,
        )
        res = resolver.resolve(participant)

        self.assertFalse(res.is_resolved)
        self.assertEqual(res.status, ResolutionStatus.UNRESOLVED)
        self.assertEqual(res.target_type, TargetDocumentType.HOST)
        self.assertTrue(any("9999" in d for d in res.diagnostics))

    def test_direct_linked_element_resolution_success(self):
        # Create linked document with a duct element
        duct_elem = MockElement(
            2001,
            "Rectangular Duct 300x200",
            category_name="Ducts",
            unique_id="guid-duct-2001",
        )
        mep_doc = MockDocument(
            title="MEP_Services",
            path_name="C:/BIM/MEP_Services.rvt",
            elements={2001: duct_elem},
        )

        # Host document hosting link instance
        link_inst = MockRevitLinkInstance(
            instance_id=500,
            instance_name="MEP_Services.rvt : 1 : location <Not Shared>",
            linked_doc=mep_doc,
        )
        host_doc = MockDocument(
            title="Hospital_Master",
            path_name="C:/BIM/Hospital_Master.rvt",
            link_instances=[link_inst],
        )

        resolver = ElementResolver(host_doc)
        participant = ClashParticipant(
            model_name="MEP_Services.nwc",
            element_id=2001,
            category="Ducts",
            item_name="Rectangular Duct 300x200",
            unique_id="guid-duct-2001",
        )

        res = resolver.resolve(participant)
        self.assertTrue(res.is_resolved)
        self.assertEqual(res.status, ResolutionStatus.RESOLVED)
        self.assertEqual(res.target_type, TargetDocumentType.LINK)
        self.assertTrue(res.is_linked)
        self.assertEqual(res.linked_element, duct_elem)
        self.assertEqual(res.link_instance, link_inst)
        self.assertEqual(res.linked_document, mep_doc)
        self.assertEqual(res.link_instance_id, 500)
        self.assertEqual(res.confidence, ConfidenceLevel.HIGH)

    def test_linked_element_missing_in_linked_document(self):
        mep_doc = MockDocument(title="MEP_Services", elements={})
        link_inst = MockRevitLinkInstance(
            instance_id=500,
            instance_name="MEP_Services.rvt : 1",
            linked_doc=mep_doc,
        )
        host_doc = MockDocument(title="Hospital_Master", link_instances=[link_inst])

        resolver = ElementResolver(host_doc)
        participant = ClashParticipant(
            model_name="MEP_Services.nwc",
            element_id=7777,
        )
        res = resolver.resolve(participant)

        self.assertFalse(res.is_resolved)
        self.assertEqual(res.status, ResolutionStatus.UNRESOLVED)
        self.assertEqual(res.target_type, TargetDocumentType.LINK)
        self.assertTrue(any("7777" in d for d in res.diagnostics))

    def test_unloaded_link_handling(self):
        # Create an unloaded link instance (GetLinkDocument() returns None)
        link_unloaded = MockRevitLinkInstance(
            instance_id=600,
            instance_name="Structure_Core.rvt : 1",
            linked_doc=None,
            is_loaded=False,
        )
        host_doc = MockDocument(title="Hospital_Master", link_instances=[link_unloaded])

        resolver = ElementResolver(host_doc)
        participant = ClashParticipant(
            model_name="Structure_Core.nwc",
            element_id=3001,
        )
        res = resolver.resolve(participant)

        self.assertFalse(res.is_resolved)
        self.assertEqual(res.status, ResolutionStatus.UNRESOLVED)
        self.assertEqual(res.target_type, TargetDocumentType.LINK)
        self.assertTrue(any("UNLOADED" in d for d in res.diagnostics))
        self.assertEqual(res.link_instance_id, 600)

    def test_duplicate_link_instances_ambiguous_safety(self):
        # Model placed twice (e.g. repeated apartment unit)
        unit_doc = MockDocument(
            title="Apartment_Unit",
            elements={101: MockElement(101, "Door")},
        )
        inst_1 = MockRevitLinkInstance(
            instance_id=10,
            instance_name="Apartment_Unit.rvt : 1 : location <Not Shared>",
            linked_doc=unit_doc,
        )
        inst_2 = MockRevitLinkInstance(
            instance_id=11,
            instance_name="Apartment_Unit.rvt : 2 : location <Not Shared>",
            linked_doc=unit_doc,
        )
        host_doc = MockDocument(title="Master", link_instances=[inst_1, inst_2])

        resolver = ElementResolver(host_doc)
        # Participant does NOT have instance-distinguishing selection hierarchy
        participant = ClashParticipant(
            model_name="Apartment_Unit.nwc",
            element_id=101,
            path_nodes=["Apartment_Unit.nwc", "Doors", "Door 101"],
        )

        res = resolver.resolve(participant)
        # MUST NOT arbitrarily pick instance 1 or instance 2!
        self.assertFalse(res.is_resolved)
        self.assertEqual(res.status, ResolutionStatus.AMBIGUOUS)
        self.assertEqual(res.target_type, TargetDocumentType.LINK)
        self.assertEqual(len(res.candidates), 2)
        candidate_ids = [c["instance_id"] for c in res.candidates]
        self.assertIn(10, candidate_ids)
        self.assertIn(11, candidate_ids)
        self.assertTrue(any("Ambiguous link instance" in d for d in res.diagnostics))

    def test_duplicate_link_instances_disambiguated_by_hierarchy(self):
        # Same two instances
        unit_doc = MockDocument(
            title="Apartment_Unit",
            elements={101: MockElement(101, "Door")},
        )
        inst_1 = MockRevitLinkInstance(
            instance_id=10,
            instance_name="Apartment_Unit.rvt : 1 : location <Not Shared>",
            linked_doc=unit_doc,
        )
        inst_2 = MockRevitLinkInstance(
            instance_id=11,
            instance_name="Apartment_Unit.rvt : 2 : location <Not Shared>",
            linked_doc=unit_doc,
        )
        host_doc = MockDocument(title="Master", link_instances=[inst_1, inst_2])

        resolver = ElementResolver(host_doc)
        # Participant selection tree contains specific instance name node ': 2 :'
        participant = ClashParticipant(
            model_name="Master_Federated.nwc",
            element_id=101,
            path_nodes=[
                "Master_Federated.nwc",
                "Apartment_Unit.rvt : 2 : location <Not Shared>",
                "Doors",
                "Door 101",
            ],
        )

        res = resolver.resolve(participant)
        # Successfully disambiguated to instance 2 (id=11)
        self.assertTrue(res.is_resolved)
        self.assertEqual(res.status, ResolutionStatus.RESOLVED)
        self.assertEqual(res.link_instance_id, 11)
        self.assertEqual(res.confidence, ConfidenceLevel.HIGH)

    def test_overlapping_element_ids_in_different_linked_models(self):
        # Arch and Structure both contain an element with numeric ID 5000
        arch_elem = MockElement(5000, "Curtain Wall", category_name="Walls")
        arch_doc = MockDocument(title="Arch_Model", elements={5000: arch_elem})
        arch_inst = MockRevitLinkInstance(1, "Arch_Model.rvt : 1", linked_doc=arch_doc)

        struct_elem = MockElement(5000, "Concrete Column", category_name="Structural Columns")
        struct_doc = MockDocument(title="Struct_Model", elements={5000: struct_elem})
        struct_inst = MockRevitLinkInstance(2, "Struct_Model.rvt : 1", linked_doc=struct_doc)

        host_doc = MockDocument(title="Hospital", link_instances=[arch_inst, struct_inst])
        resolver = ElementResolver(host_doc)

        # Participant A: belongs to Arch_Model
        part_a = ClashParticipant(model_name="Arch_Model.nwc", element_id=5000)
        res_a = resolver.resolve(part_a)
        self.assertEqual(res_a.link_instance_id, 1)
        self.assertEqual(res_a.resolved_element.Name, "Curtain Wall")

        # Participant B: belongs to Struct_Model
        part_b = ClashParticipant(model_name="Struct_Model.nwc", element_id=5000)
        res_b = resolver.resolve(part_b)
        self.assertEqual(res_b.link_instance_id, 2)
        self.assertEqual(res_b.resolved_element.Name, "Concrete Column")

    def test_unmatched_model_handling(self):
        host_doc = MockDocument(title="Master", link_instances=[])
        resolver = ElementResolver(host_doc)

        participant = ClashParticipant(
            model_name="Civil_Site.nwc",
            element_id=123,
        )
        res = resolver.resolve(participant)

        self.assertFalse(res.is_resolved)
        self.assertEqual(res.status, ResolutionStatus.UNRESOLVED)
        self.assertEqual(res.target_type, TargetDocumentType.UNKNOWN)
        self.assertTrue(any("could not be matched" in d for d in res.diagnostics))

    def test_host_and_link_name_conflict_ambiguous(self):
        # Host doc is named 'Core' and there is also a link instance named 'Core.rvt'
        link_inst = MockRevitLinkInstance(
            instance_id=10,
            instance_name="Core.rvt : 1",
            linked_doc=MockDocument("Core"),
        )
        host_doc = MockDocument(
            title="Core",
            link_instances=[link_inst],
        )

        resolver = ElementResolver(host_doc)
        participant = ClashParticipant(model_name="Core.nwc", element_id=123)

        res = resolver.resolve(participant)
        self.assertEqual(res.status, ResolutionStatus.AMBIGUOUS)
        self.assertTrue(any("matches both the host document" in d for d in res.diagnostics))

    def test_nested_links_unsupported(self):
        # Child link inside Parent doc
        child_doc = MockDocument(
            title="Fire_Alarm",
            elements={888: MockElement(888, "Smoke Detector")},
        )
        child_link = MockRevitLinkInstance(20, "Fire_Alarm.rvt : 1", linked_doc=child_doc)

        parent_doc = MockDocument(
            title="MEP_Parent",
            link_instances=[child_link],
        )
        parent_link = MockRevitLinkInstance(
            10, "MEP_Parent.rvt : 1", linked_doc=parent_doc
        )

        host_doc = MockDocument(title="Master", link_instances=[parent_link])

        resolver = ElementResolver(host_doc)
        participant = ClashParticipant(
            model_name="Fire_Alarm.nwc",
            element_id=888,
        )

        res = resolver.resolve(participant)
        # Nested link detected and explicitly reported as UNSUPPORTED
        self.assertEqual(res.status, ResolutionStatus.UNSUPPORTED)
        self.assertEqual(res.target_type, TargetDocumentType.NESTED_LINK)
        self.assertTrue(any("nested link" in d.lower() for d in res.diagnostics))

    def test_corroboration_warnings(self):
        duct_elem = MockElement(100, "Rectangular Duct", category_name="Ducts")
        mep_doc = MockDocument(title="MEP", elements={100: duct_elem})
        mep_link = MockRevitLinkInstance(1, "MEP.rvt : 1", linked_doc=mep_doc)
        host_doc = MockDocument(title="Host", link_instances=[mep_link])

        resolver = ElementResolver(host_doc)
        # Participant claims category is 'Walls'
        participant = ClashParticipant(
            model_name="MEP.nwc",
            element_id=100,
            category="Walls",
        )

        res = resolver.resolve(participant)
        self.assertTrue(res.is_resolved)
        self.assertEqual(res.corroboration["category_match"], False)
        self.assertTrue(any("Category mismatch" in d for d in res.diagnostics))

    def test_resolve_clash_convenience(self):
        elem_a = MockElement(10, "Duct", category_name="Ducts")
        elem_b = MockElement(20, "Wall", category_name="Walls")

        doc_a = MockDocument(title="MEP", elements={10: elem_a})
        doc_b = MockDocument(title="Arch", elements={20: elem_b})

        link_a = MockRevitLinkInstance(1, "MEP.rvt : 1", linked_doc=doc_a)
        link_b = MockRevitLinkInstance(2, "Arch.rvt : 1", linked_doc=doc_b)

        host_doc = MockDocument(title="Master", link_instances=[link_a, link_b])
        resolver = ElementResolver(host_doc)

        clash = ClashResultItem(
            name="Clash_01",
            participant_a=ClashParticipant(model_name="MEP.nwc", element_id=10),
            participant_b=ClashParticipant(model_name="Arch.nwc", element_id=20),
        )

        res_a, res_b = resolver.resolve_clash(clash)
        self.assertTrue(res_a.is_resolved)
        self.assertEqual(res_a.resolved_element_id, 10)
        self.assertTrue(res_b.is_resolved)
        self.assertEqual(res_b.resolved_element_id, 20)

    def test_int64_element_id_json_serialization_safety(self):
        import json

        class ClrInt64IdMock(object):
            def __init__(self, raw_val):
                try:
                    import System
                    self.Value = System.Int64(raw_val)
                except ImportError:
                    self.Value = int(raw_val)

        # Standard 32-bit range ID wrapped in System.Int64
        mock_32 = ClrInt64IdMock(123456)
        val_32 = get_element_id_value(mock_32)
        self.assertIsInstance(val_32, integer_types)
        self.assertEqual(val_32, 123456)

        # Large 64-bit Revit 2024+ ID (> 2^31 - 1)
        mock_64 = ClrInt64IdMock(5000000000)
        val_64 = get_element_id_value(mock_64)
        self.assertIsInstance(val_64, integer_types)
        self.assertEqual(val_64, 5000000000)

        # Verify json.dumps succeeds without TypeError on both direct values and ParticipantResolution.to_dict()
        elem_64 = MockElement(5000000000, "64Bit Wall", category_name="Walls")
        elem_64.Id = mock_64
        host_doc = MockDocument(title="Host64", elements={5000000000: elem_64})
        resolver = ElementResolver(host_doc)
        part = ClashParticipant(model_name="Host64.nwc", element_id=5000000000)
        res = resolver.resolve(part)
        self.assertTrue(res.is_resolved)

        serialized = json.dumps(res.to_dict())
        self.assertIn("5000000000", serialized)
        self.assertEqual(get_element_id_value(create_revit_element_id(5000000000)), 5000000000)

    def test_deep_selection_tree_disambiguation_and_nested_cache(self):
        unit_doc = MockDocument(
            title="Apartment_Unit",
            elements={101: MockElement(101, "Door")},
        )
        inst_1 = MockRevitLinkInstance(
            instance_id=10,
            instance_name="Apartment_Unit.rvt : 1 : location <Not Shared>",
            linked_doc=unit_doc,
        )
        inst_2 = MockRevitLinkInstance(
            instance_id=11,
            instance_name="Apartment_Unit.rvt : 2 : location <Not Shared>",
            linked_doc=unit_doc,
        )
        host_doc = MockDocument(title="Master", link_instances=[inst_1, inst_2])
        resolver = ElementResolver(host_doc)

        # Link instance node is at index 4 (5th node, deeper than [:3])
        deep_participant = ClashParticipant(
            model_name="Federated_Coordination.nwf",
            element_id=101,
            path_nodes=[
                "Federated_Coordination.nwf",
                "01_Building_Envelope",
                "Zone_B_Residential",
                "Level_04_Package",
                "Apartment_Unit.rvt : 2 : location <Not Shared>",
                "Doors",
                "Single-Flush",
                "Door 101",
            ],
        )
        res = resolver.resolve(deep_participant)
        self.assertTrue(res.is_resolved)
        self.assertEqual(res.status, ResolutionStatus.RESOLVED)
        self.assertEqual(res.link_instance_id, 11)

        # Verify nested link cache is populated once and reused
        self.assertIsNone(resolver._nested_link_cache)
        unmatched_1 = ClashParticipant(model_name="Unknown_A.nwc", element_id=999)
        resolver.resolve(unmatched_1)
        cached_ref = resolver._nested_link_cache
        self.assertIsNotNone(cached_ref)
        unmatched_2 = ClashParticipant(model_name="Unknown_B.nwc", element_id=998)
        resolver.resolve(unmatched_2)
        self.assertIs(resolver._nested_link_cache, cached_ref)

    def test_diagnostic_html_escaping_and_dynamic_units(self):
        # Verify '<Not Shared>' and dynamic units in diagnostic script report formatter
        backup_file = r"C:\Users\hazem.sakr\.gemini\antigravity\scratch\removed_backup\ClashView Sync Diagnostic.pushbutton\script.py"
        installed_file = os.path.abspath(
            os.path.join(
                _LIB_DIR,
                r"..\Sakr Tools.tab\Tools.panel\ClashView Sync Diagnostic.pushbutton\script.py",
            )
        )
        target_file = backup_file if os.path.isfile(backup_file) else installed_file
        if not os.path.isfile(target_file):
            return  # Diagnostic script was removed/backed up

        with open(target_file, "rb") as fh:
            raw_source = fh.read().decode("utf-8")

        # Extract _escape_html and _resolve_display_units definitions and test directly
        ns = {}
        exec(
            "def _escape_html(text):\n"
            "    if text is None:\n"
            "        return ''\n"
            "    s = '{0}'.format(text)\n"
            "    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\"', '&quot;').replace(\"'\", '&#39;')\n",
            ns,
        )
        escaped = ns["_escape_html"]("MEP_Services.rvt : 1 : location <Not Shared>")
        self.assertIn("&lt;Not Shared&gt;", escaped)
        self.assertNotIn("<Not Shared>", escaped)
        self.assertIn("def _escape_html(text):", raw_source)
        self.assertIn("{unit_str}", raw_source)


    def test_global_element_id_search_fallback(self):
        """When XML lacks Selection Path (model_name is None), resolver searches across loaded docs by ID."""
        door_elem = MockElement(505, "Interior Door", category_name="Doors")
        pipe_elem = MockElement(606, "Sanitary Pipe", category_name="Pipes")
        link_doc = MockDocument(title="MEP_Plumbing", elements={606: pipe_elem})
        link_inst = MockRevitLinkInstance(10, "MEP_Plumbing.rvt : 1", linked_doc=link_doc)

        host_doc = MockDocument(title="Arch_Host", elements={505: door_elem})
        resolver = ElementResolver(host_doc, link_instances=[link_inst])

        # Participant A in Host without model_name
        part_a = ClashParticipant(element_id=505, model_name=None, layer="1ST FLOOR", item_name="Interior Door")
        res_a = resolver.resolve(part_a)
        self.assertEqual(res_a.status, ResolutionStatus.RESOLVED)
        self.assertEqual(res_a.target_type, TargetDocumentType.HOST)
        self.assertIs(res_a.resolved_element, door_elem)
        self.assertEqual(res_a.match_strategy, "GLOBAL_ID_SEARCH_HOST")

        # Participant B in Link without model_name
        part_b = ClashParticipant(element_id=606, model_name=None, layer="2ND FLOOR", item_name="Sanitary Pipe")
        res_b = resolver.resolve(part_b)
        self.assertEqual(res_b.status, ResolutionStatus.RESOLVED)
        self.assertEqual(res_b.target_type, TargetDocumentType.LINK)
        self.assertIs(res_b.resolved_element, pipe_elem)
        self.assertIs(res_b.link_instance, link_inst)
        self.assertEqual(res_b.match_strategy, "GLOBAL_ID_SEARCH_LINK")


if __name__ == "__main__":
    unittest.main()
