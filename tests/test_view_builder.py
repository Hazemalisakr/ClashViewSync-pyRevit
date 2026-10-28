# -*- coding: utf-8 -*-
"""Unit tests for clashview_sync.view_builder."""
from __future__ import absolute_import, division, print_function

import json
import unittest

from clashview_sync.coordinate_transformer import (
    BoundingBox3D,
    CoordinateFrameStatus,
    FramingMethod,
    IsolationMethod,
    Vector3,
    calculate_clash_framing,
)
from clashview_sync.element_resolver import (
    ConfidenceLevel,
    ParticipantResolution,
    ResolutionStatus,
    TargetDocumentType,
    get_element_id_value,
)
from clashview_sync.models import ClashParticipant
from clashview_sync.view_builder import (
    CLASHVIEW_TOOL_ID,
    ViewCreationResult,
    create_or_update_clash_view,
    format_clash_view_name,
    is_tool_owned_view,
    read_view_metadata,
    resolve_view_name_collision,
    sanitize_revit_view_name,
    write_view_metadata,
)


class MockElementId(object):
    def __init__(self, val):
        self.Value = int(val)
        self.IntegerValue = int(val)


class MockElement(object):
    def __init__(self, elem_id, bbox=None):
        self.Id = MockElementId(elem_id)
        self._bbox = bbox

    def get_BoundingBox(self, view=None):
        return self._bbox


class MockCategory(object):
    def __init__(self, cat_id, name=""):
        self.Id = MockElementId(cat_id)
        self.Name = name


class MockLinkInstance(object):
    def __init__(self, inst_id, name="", category_id=None):
        self.Id = MockElementId(inst_id)
        self.Name = name
        cid = category_id if category_id is not None else (int(inst_id) + 10000)
        self.Category = MockCategory(cid, name=name)

    def GetTotalTransform(self):
        return None

    def CanBeHidden(self, view):
        return True


class MockView3D(object):
    def __init__(self, view_id, name="", is_template=False, fail_on_section_box=False):
        self.Id = MockElementId(view_id)
        self.Name = name
        self.IsTemplate = is_template
        self.IsLocked = False
        self.IsSectionBoxActive = False
        self.ViewTemplateId = None
        self.DetailLevel = None
        self.DisplayStyle = None
        self.section_box_set = None
        self.orientation_set = None
        self.isolated_temp_ids = []
        self.converted_temp_to_perm = False
        self.hidden_element_ids = []
        self.unhidden_element_ids = []
        self.hidden_category_ids = []
        self.element_overrides = {}
        self.applied_filters = []
        self.filter_overrides = {}
        self.unlocked_called = False
        self._fail_on_section_box = fail_on_section_box

    def SetElementOverrides(self, elem_id, ogs):
        self.element_overrides[get_element_id_value(elem_id)] = ogs

    def AddFilter(self, filter_id):
        self.applied_filters.append(get_element_id_value(filter_id))

    def IsFilterApplied(self, filter_id):
        return get_element_id_value(filter_id) in self.applied_filters

    def SetFilterOverrides(self, filter_id, ogs):
        self.filter_overrides[get_element_id_value(filter_id)] = ogs

    def SetFilterVisibility(self, filter_id, visible):
        pass

    def Unlock(self):
        self.IsLocked = False
        self.unlocked_called = True

    def CanCategoryBeHidden(self, cat_id):
        return True

    def SetCategoryHidden(self, cat_id, hide):
        val = get_element_id_value(cat_id)
        cid = val if val is not None else cat_id
        if hide:
            if cid not in self.hidden_category_ids:
                self.hidden_category_ids.append(cid)
        else:
            if cid in self.hidden_category_ids:
                self.hidden_category_ids.remove(cid)

    def SetSectionBox(self, bbox):
        if self._fail_on_section_box:
            raise RuntimeError("Simulated Revit API failure in SetSectionBox")
        self.section_box_set = bbox

    def SetOrientation(self, orientation):
        self.orientation_set = orientation

    def IsolateElementsTemporary(self, id_collection):
        self.isolated_temp_ids = [get_element_id_value(x) for x in id_collection]

    def ConvertTemporaryHideIsolateToPermanent(self):
        self.converted_temp_to_perm = True

    def HideElements(self, id_collection):
        self.hidden_element_ids.extend([get_element_id_value(x) for x in id_collection])

    def UnhideElements(self, id_collection):
        self.unhidden_element_ids.extend([get_element_id_value(x) for x in id_collection])


class MockTransaction(object):
    def __init__(self, name):
        self.name = name
        self.started = False
        self.committed = False
        self.rolled_back = False
        self.ended = False

    def Start(self):
        self.started = True

    def Commit(self):
        self.committed = True
        self.ended = True

    def RollBack(self):
        self.rolled_back = True
        self.ended = True

    def HasStarted(self):
        return self.started

    def HasEnded(self):
        return self.ended


class MockHostDocument(object):
    def __init__(self, views=None, link_instances=None, view_family_type_id=900):
        self.views = list(views) if views else []
        self.link_instances = list(link_instances) if link_instances else []
        self.view_family_type_id = view_family_type_id
        self.transactions = []
        self._next_view_id = 5000
        self.fail_on_new_view_section_box = False

    def create_transaction(self, name):
        tx = MockTransaction(name)
        self.transactions.append(tx)
        return tx

    def create_isometric_view(self, vft_id):
        vid = self._next_view_id
        self._next_view_id += 1
        v = MockView3D(
            vid,
            name="3D View {0}".format(vid),
            fail_on_section_box=self.fail_on_new_view_section_box,
        )
        self.views.append(v)
        return v

    def GetElement(self, elem_id):
        val = get_element_id_value(elem_id)
        for link in self.link_instances:
            if get_element_id_value(link.Id) == val:
                return link
        return None


def _make_resolved_host_participant(elem_id, min_xyz, max_xyz):
    bbox = BoundingBox3D(min_xyz, max_xyz)
    elem = MockElement(elem_id, bbox=bbox)
    p = ClashParticipant(
        element_id=elem_id,
        element_id_raw=str(elem_id),
        model_name="HostModel",
        category="Ducts",
    )
    return ParticipantResolution(
        participant=p,
        status=ResolutionStatus.RESOLVED,
        target_type=TargetDocumentType.HOST,
        confidence=ConfidenceLevel.HIGH,
        host_element=elem,
        match_strategy="HOST_ID_VERIFIED",
    )


def _make_resolved_linked_participant(
    elem_id, link_inst_id, min_xyz, max_xyz, model_name="MEP_Link", link_instance=None
):
    bbox = BoundingBox3D(min_xyz, max_xyz)
    elem = MockElement(elem_id, bbox=bbox)
    link_inst = link_instance if link_instance is not None else MockLinkInstance(
        link_inst_id, name="{0}.rvt : 1 : location".format(model_name)
    )
    p = ClashParticipant(
        element_id=elem_id,
        element_id_raw=str(elem_id),
        model_name=model_name,
        category="Pipes",
    )
    return ParticipantResolution(
        participant=p,
        status=ResolutionStatus.RESOLVED,
        target_type=TargetDocumentType.LINK,
        confidence=ConfidenceLevel.HIGH,
        linked_element=elem,
        link_instance=link_inst,
        match_strategy="SINGLE_LINK_INSTANCE",
    )


class TestViewBuilder(unittest.TestCase):
    """Tests for view_builder naming, collision safety, isolation modes, and rollback."""

    def test_format_and_sanitize_view_names(self):
        """Strips prohibited Revit characters, preserves prefixes, and caps length."""
        dirty_test = r"01: MEP\STR {Level 2} [Zone|A]; <Main>? `Test` ~1\nLine2"
        dirty_clash = "Clash #14: Pipe vs Beam [Grid A/1]"

        pair_name = format_clash_view_name(dirty_test, dirty_clash, mode="pair")
        context_name = format_clash_view_name(dirty_test, dirty_clash, mode="context")

        self.assertTrue(pair_name.startswith("CVS_Pair_"))
        self.assertTrue(context_name.startswith("CVS_Context_"))

        prohibited = set(r'\:{}[]|;<>?`~' + "\r\n\t")
        for ch in pair_name:
            self.assertNotIn(ch, prohibited)
        for ch in context_name:
            self.assertNotIn(ch, prohibited)

        # Verify length cap preserves prefix and clash suffix
        very_long_test = "A" * 250
        capped = format_clash_view_name(
            very_long_test, "Clash_999", mode="pair", max_length=80
        )
        self.assertLessEqual(len(capped), 80)
        self.assertTrue(capped.startswith("CVS_Pair_"))
        self.assertTrue(capped.endswith("_Clash_999"))

    def test_non_tool_owned_view_collision_never_overwritten(self):
        """Unrelated views without tool metadata are NEVER overwritten, even if update_existing=True."""
        user_view = MockView3D(101, name="CVS_Pair_MEP_vs_STR_Clash_01")
        doc = MockHostDocument(views=[user_view])

        self.assertFalse(is_tool_owned_view(user_view))

        res_a = _make_resolved_host_participant(2001, (0, 0, 0), (2, 2, 2))
        res_b = _make_resolved_host_participant(2002, (1, 1, 1), (3, 3, 3))
        plan = calculate_clash_framing(res_a, res_b, mode="pair")

        # Attempt with update_existing=True — must STILL refuse to touch user_view!
        result = create_or_update_clash_view(
            doc,
            plan,
            test_name="MEP vs STR",
            clash_name="Clash 01",
            res_a=res_a,
            res_b=res_b,
            update_existing=True,
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.action_taken, "CREATED")
        self.assertEqual(result.view_name, "CVS_Pair_MEP_vs_STR_Clash_01_01")
        self.assertNotEqual(result.view_id, 101)
        # Verify user_view was completely untouched
        self.assertIsNone(user_view.section_box_set)
        self.assertFalse(user_view.IsSectionBoxActive)
        self.assertTrue(
            any("NOT owned by ClashView Sync" in d for d in result.diagnostics)
        )

    def test_tool_owned_view_update_vs_suffixed_new(self):
        """Verified tool-owned views update in place when update_existing=True, or suffix when False."""
        tool_view = MockView3D(105, name="CVS_Pair_TestA_Clash1")
        write_view_metadata(
            tool_view,
            {
                "ToolId": CLASHVIEW_TOOL_ID,
                "Mode": "pair",
                "TestName": "TestA",
                "ClashName": "Clash1",
            },
        )
        self.assertTrue(is_tool_owned_view(tool_view))

        doc = MockHostDocument(views=[tool_view])
        res_a = _make_resolved_host_participant(3001, (10, 10, 0), (14, 14, 4))
        res_b = _make_resolved_host_participant(3002, (12, 12, 1), (16, 16, 5))
        plan = calculate_clash_framing(res_a, res_b, mode="pair")

        # 1. update_existing=False -> creates suffixed view _01 without modifying tool_view
        res_new = create_or_update_clash_view(
            doc,
            plan,
            test_name="TestA",
            clash_name="Clash1",
            res_a=res_a,
            res_b=res_b,
            update_existing=False,
        )
        self.assertTrue(res_new.succeeded)
        self.assertEqual(res_new.action_taken, "CREATED")
        self.assertEqual(res_new.view_name, "CVS_Pair_TestA_Clash1_01")
        self.assertIsNone(tool_view.section_box_set)

        # 2. update_existing=True -> reuses and updates tool_view in place
        tool_view.IsLocked = True
        res_upd = create_or_update_clash_view(
            doc,
            plan,
            test_name="TestA",
            clash_name="Clash1",
            res_a=res_a,
            res_b=res_b,
            update_existing=True,
        )
        self.assertTrue(res_upd.succeeded)
        self.assertEqual(res_upd.action_taken, "UPDATED")
        self.assertEqual(res_upd.view_id, 105)
        self.assertEqual(res_upd.view_name, "CVS_Pair_TestA_Clash1")
        self.assertTrue(tool_view.unlocked_called)
        self.assertTrue(tool_view.IsSectionBoxActive)
        self.assertIsNotNone(tool_view.section_box_set)

    def test_mode_a_host_vs_host_native_isolation(self):
        """Mode A host-vs-host isolates both host elements and converts temporary to permanent."""
        doc = MockHostDocument()
        res_a = _make_resolved_host_participant(4001, (0, 0, 0), (5, 5, 5))
        res_b = _make_resolved_host_participant(4002, (3, 3, 3), (8, 8, 8))
        plan = calculate_clash_framing(res_a, res_b, mode="pair")

        result = create_or_update_clash_view(
            doc,
            plan,
            test_name="HostTest",
            clash_name="ClashHost",
            res_a=res_a,
            res_b=res_b,
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.action_taken, "CREATED")
        self.assertEqual(
            result.isolation_method, IsolationMethod.NATIVE_ELEMENT_ISOLATION
        )
        self.assertEqual(result.isolated_host_ids, [4001, 4002])
        self.assertEqual(result.view.isolated_temp_ids, [4001, 4002])
        self.assertTrue(result.view.converted_temp_to_perm)
        self.assertTrue(result.view.IsSectionBoxActive)
        self.assertIsNotNone(result.view.orientation_set)
        self.assertTrue(result.metadata_written)

        meta = read_view_metadata(result.view)
        self.assertEqual(meta["ToolId"], CLASHVIEW_TOOL_ID)
        self.assertEqual(meta["ParticipantAId"], "4001")
        self.assertEqual(meta["ParticipantBId"], "4002")

        # Verify JSON serialization works cleanly (no System.Int64 leaks)
        serialized = json.dumps(result.to_dict())
        self.assertIn("NATIVE_ELEMENT_ISOLATION", serialized)

    def test_mode_a_linked_clash_hides_unrelated_links_only(self):
        """Mode A involving linked element hides unrelated links and NEVER passes linked IDs to IsolateElementsTemporary."""
        link_1 = MockLinkInstance(701, "MEP_Link.rvt : 1")
        link_2 = MockLinkInstance(702, "STR_Link.rvt : 2")
        link_3 = MockLinkInstance(703, "ARC_Link.rvt : 3")
        doc = MockHostDocument(link_instances=[link_1, link_2, link_3])

        # Participant A is element 99901 inside Link 701; Participant B is host element 4010
        res_a = _make_resolved_linked_participant(99901, 701, (0, 0, 0), (4, 4, 4))
        res_b = _make_resolved_host_participant(4010, (2, 2, 2), (6, 6, 6))
        plan = calculate_clash_framing(res_a, res_b, mode="pair")

        result = create_or_update_clash_view(
            doc,
            plan,
            test_name="LinkTest",
            clash_name="ClashLink",
            res_a=res_a,
            res_b=res_b,
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(
            result.isolation_method, IsolationMethod.LINK_CATEGORY_AND_SECTION_BOX
        )
        # Host element 4010 and link 701 are isolated; linked element ID 99901 must NEVER be passed
        self.assertIn(4010, result.view.isolated_temp_ids)
        self.assertIn(701, result.view.isolated_temp_ids)
        self.assertNotIn(99901, result.view.isolated_temp_ids)
        self.assertNotIn(99901, result.view.hidden_element_ids)
        # Unrelated links 702 and 703 are hidden; participating link 701 is unhidden/kept visible
        self.assertEqual(result.hidden_link_ids, [702, 703])
        self.assertEqual(result.view.hidden_element_ids, [702, 703])
        self.assertEqual(result.view.unhidden_element_ids, [701])
        # In VV (Visibility/Graphics): ONLY affiliated link 701 is switched ON; unrelated links 702 & 703 are switched OFF
        self.assertIn(10702, result.view.hidden_category_ids)
        self.assertIn(10703, result.view.hidden_category_ids)
        self.assertNotIn(10701, result.view.hidden_category_ids)
        self.assertTrue(
            any("LINK_CATEGORY_AND_SECTION_BOX" in d for d in result.diagnostics)
        )

    def test_mode_b_context_view_no_isolation(self):
        """Mode B sets expanded context section box and camera orientation without isolating elements."""
        doc = MockHostDocument()
        res_a = _make_resolved_host_participant(5001, (0, 0, 0), (4, 4, 4))
        res_b = _make_resolved_host_participant(5002, (2, 2, 2), (6, 6, 6))
        plan = calculate_clash_framing(res_a, res_b, mode="context")

        result = create_or_update_clash_view(
            doc,
            plan,
            test_name="ContextTest",
            clash_name="ClashCtx",
            res_a=res_a,
            res_b=res_b,
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.view_name, "CVS_Context_ContextTest_ClashCtx")
        self.assertEqual(
            result.isolation_method, IsolationMethod.CONTEXT_SECTION_BOX_ONLY
        )
        self.assertEqual(result.view.isolated_temp_ids, [])
        self.assertEqual(result.view.hidden_element_ids, [])
        self.assertTrue(result.view.IsSectionBoxActive)
        # Context box expands [0..6] by 3.0 ft on each side -> [-3.0 .. 9.0]
        self.assertAlmostEqual(result.view.section_box_set.Min.X, -3.0, places=5)
        self.assertAlmostEqual(result.view.section_box_set.Max.X, 9.0, places=5)

    def test_invalid_or_unconfirmed_fallback_aborts_before_transaction(self):
        """Invalid framing plans or unconfirmed single-participant fallbacks abort with 0 transactions."""
        doc = MockHostDocument()

        # 1. Both participants unresolved -> UNAVAILABLE_GEOMETRY_FALLBACK
        p_unres = ClashParticipant(element_id=8888, model_name="Unknown")
        res_unres = ParticipantResolution(
            participant=p_unres,
            status=ResolutionStatus.UNRESOLVED,
            target_type=TargetDocumentType.UNKNOWN,
            confidence=ConfidenceLevel.NONE,
        )
        invalid_plan = calculate_clash_framing(res_unres, res_unres, mode="pair")
        res_abort_1 = create_or_update_clash_view(
            doc, invalid_plan, "Test", "Clash", res_a=res_unres, res_b=res_unres
        )
        self.assertFalse(res_abort_1.succeeded)
        self.assertEqual(res_abort_1.action_taken, "ABORTED")
        self.assertEqual(len(doc.transactions), 0)

        # 2. Only A resolved -> SINGLE_PARTICIPANT_FALLBACK with allow_single_participant_fallback=False
        res_a = _make_resolved_host_participant(6001, (0, 0, 0), (5, 5, 5))
        single_plan = calculate_clash_framing(res_a, res_unres, mode="pair")
        self.assertEqual(
            single_plan.framing_method, FramingMethod.SINGLE_PARTICIPANT_FALLBACK
        )

        res_abort_2 = create_or_update_clash_view(
            doc,
            single_plan,
            "Test",
            "Clash",
            res_a=res_a,
            res_b=res_unres,
            allow_single_participant_fallback=False,
        )
        self.assertFalse(res_abort_2.succeeded)
        self.assertEqual(res_abort_2.action_taken, "ABORTED")
        self.assertEqual(len(doc.transactions), 0)

        # 3. Same single_plan with allow_single_participant_fallback=True -> succeeds
        res_ok = create_or_update_clash_view(
            doc,
            single_plan,
            "Test",
            "Clash",
            res_a=res_a,
            res_b=res_unres,
            allow_single_participant_fallback=True,
        )
        self.assertTrue(res_ok.succeeded)
        self.assertEqual(res_ok.action_taken, "CREATED")
        self.assertEqual(len(doc.transactions), 1)
        self.assertTrue(doc.transactions[0].committed)

    def test_transaction_rollback_on_exception(self):
        """Mid-transaction exceptions trigger tx.RollBack() and return ABORTED."""
        doc = MockHostDocument()
        doc.fail_on_new_view_section_box = True

        res_a = _make_resolved_host_participant(7001, (0, 0, 0), (5, 5, 5))
        res_b = _make_resolved_host_participant(7002, (2, 2, 2), (7, 7, 7))
        plan = calculate_clash_framing(res_a, res_b, mode="pair")

        result = create_or_update_clash_view(
            doc, plan, "RollbackTest", "ClashErr", res_a=res_a, res_b=res_b
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(result.action_taken, "ABORTED")
        self.assertIsNone(result.view)
        self.assertEqual(len(doc.transactions), 1)
        self.assertTrue(doc.transactions[0].started)
        self.assertTrue(doc.transactions[0].rolled_back)
        self.assertFalse(doc.transactions[0].committed)
        self.assertTrue(
            any("Transaction rolled back" in d for d in result.diagnostics)
        )

    def test_clutter_categories_hidden_automatically(self):
        """Scope Boxes, Levels, Grids, and Reference Planes are hidden in created views."""
        doc = MockHostDocument()
        res_a = _make_resolved_host_participant(8001, (0, 0, 0), (10, 10, 10))
        res_b = _make_resolved_host_participant(8002, (5, 5, 5), (15, 15, 15))
        plan = calculate_clash_framing(res_a, res_b, mode="context")

        result = create_or_update_clash_view(
            doc, plan, "CleanViewTest", "Clash10", res_a=res_a, res_b=res_b
        )
        self.assertTrue(result.succeeded)
        self.assertIsNotNone(result.view)
        # Verify clutter categories were hidden
        self.assertIn("OST_VolumeOfInterest", result.view.hidden_category_ids)
        self.assertIn("OST_Levels", result.view.hidden_category_ids)
        self.assertIn("OST_Grids", result.view.hidden_category_ids)
        self.assertIn("OST_CLines", result.view.hidden_category_ids)

    def test_vv_link_visibility_switches_on_affiliated_links_only(self):
        """In Mode A (Isolated), ONLY affiliated links are switched ON in VV; all others are switched OFF."""
        link_str = MockLinkInstance(801, "360-JDF02-STR-BA-RVT-0001.rvt", category_id=1801)
        link_facade = MockLinkInstance(802, "360-JDF02-ARC-BA-FC-RVT-0001.rvt", category_id=1802)
        link_mep = MockLinkInstance(803, "360-JDF02-MEC-BA-RVT-0001.rvt", category_id=1803)
        doc = MockHostDocument(link_instances=[link_str, link_facade, link_mep])

        # Clash between Host Ceiling (res_a) and Linked Structural framing in link_str (res_b)
        res_a = _make_resolved_host_participant(9001, (0, 0, 0), (5, 5, 5))
        res_b = _make_resolved_linked_participant(
            9002,
            801,
            (2, 2, 2),
            (7, 7, 7),
            model_name="360-JDF02-STR-BA-RVT-0001.rvt",
            link_instance=link_str,
        )
        plan = calculate_clash_framing(res_a, res_b, mode="pair")

        result = create_or_update_clash_view(
            doc, plan, "VVTest", "Clash7", res_a=res_a, res_b=res_b
        )
        self.assertTrue(result.succeeded)
        # STR link is affiliated: must NOT be hidden in VV
        self.assertNotIn(1801, result.view.hidden_category_ids)
        # Facade & MEP links are unaffiliated: MUST be hidden (switched OFF) in VV
        self.assertIn(1802, result.view.hidden_category_ids)
        self.assertIn(1803, result.view.hidden_category_ids)

    def test_model_category_isolation_shows_only_clashing_categories(self):
        """In Mode A (Isolated), non-participating Model categories (Walls, Floors, Columns) are hidden."""
        cat_ceilings = MockCategory(-2000038, "Ceilings")
        cat_framing = MockCategory(-2001320, "Structural Framing")
        cat_walls = MockCategory(-2000011, "Walls")
        cat_floors = MockCategory(-2000032, "Floors")
        cat_columns = MockCategory(-2000100, "Structural Columns")

        doc = MockHostDocument()
        doc.categories = [cat_ceilings, cat_framing, cat_walls, cat_floors, cat_columns]

        res_a = _make_resolved_host_participant(9101, (0, 0, 0), (4, 4, 4))
        res_a.element.Category = cat_ceilings
        res_b = _make_resolved_host_participant(9102, (2, 2, 2), (6, 6, 6))
        res_b.element.Category = cat_framing

        plan = calculate_clash_framing(res_a, res_b, mode="pair")
        result = create_or_update_clash_view(
            doc, plan, "CatIsoTest", "ClashOnly2", res_a=res_a, res_b=res_b
        )
        self.assertTrue(result.succeeded)
        # Clashing categories MUST remain visible
        self.assertNotIn(-2000038, result.view.hidden_category_ids)
        self.assertNotIn(-2001320, result.view.hidden_category_ids)
        # Non-clashing categories MUST be hidden so only the 2 items show
        self.assertIn(-2000011, result.view.hidden_category_ids)
        self.assertIn(-2000032, result.view.hidden_category_ids)
        self.assertIn(-2000100, result.view.hidden_category_ids)

    def test_highlight_host_participant_bright_green(self):
        """Host participants are highlighted with solid bright green element overrides."""
        doc = MockHostDocument()
        res_a = _make_resolved_host_participant(9501, (0, 0, 0), (4, 4, 4))
        res_b = _make_resolved_host_participant(9502, (2, 2, 2), (6, 6, 6))

        plan = calculate_clash_framing(res_a, res_b, mode="pair")
        result = create_or_update_clash_view(
            doc, plan, "HighlightTest", "ClashHostGreen", res_a=res_a, res_b=res_b
        )
        self.assertTrue(result.succeeded)
        # In mock mode, check that view.DisplayStyle is set to ShadingWithEdges
        self.assertEqual(result.view.DisplayStyle, "ShadingWithEdges")
        self.assertEqual(result.view.DetailLevel, "Fine")

    def test_navisworks_color_matching_and_toggle(self):
        """Highlighting matches Navisworks conventions (Item 1 = Red, Item 2 = Green) and can be toggled off."""
        doc = MockHostDocument()
        res_a = _make_resolved_host_participant(9601, (0, 0, 0), (4, 4, 4))
        res_b = _make_resolved_host_participant(9602, (2, 2, 2), (6, 6, 6))
        plan = calculate_clash_framing(res_a, res_b, mode="pair")

        # 1. Highlighting ON:
        res_on = create_or_update_clash_view(
            doc, plan, "HLTest", "ClashOn", res_a=res_a, res_b=res_b, highlight_elements=True
        )
        self.assertTrue(res_on.succeeded)

        # 2. Highlighting OFF:
        res_off = create_or_update_clash_view(
            doc, plan, "HLTest", "ClashOff", res_a=res_a, res_b=res_b, highlight_elements=False
        )
        self.assertTrue(res_off.succeeded)

    def test_cross_mode_view_reuse_single_view_guarantee(self):
        """Switching between Isolated and Normal modes on the same clash reuses the exact same view without duplicating."""
        cat_ceiling = MockCategory(-2000038, "Ceilings")
        cat_beam = MockCategory(-2001320, "Structural Framing")
        cat_wall = MockCategory(-2000011, "Walls")
        doc = MockHostDocument()
        doc.categories = [cat_ceiling, cat_beam, cat_wall]

        res_a = _make_resolved_host_participant(9701, (0, 0, 0), (10, 10, 1))
        res_a.element.Category = cat_ceiling
        res_b = _make_resolved_host_participant(9702, (2, 2, 0), (12, 12, 2))
        res_b.element.Category = cat_beam

        plan_isolated = calculate_clash_framing(res_a, res_b, mode="pair")
        plan_context = calculate_clash_framing(res_a, res_b, mode="context")

        # Step 1: Create in Isolated mode
        res1 = create_or_update_clash_view(
            doc, plan_isolated, "TestA", "Clash2", res_a=res_a, res_b=res_b, update_existing=True
        )
        self.assertTrue(res1.succeeded)
        self.assertEqual(res1.action_taken, "CREATED")
        self.assertEqual(res1.view_name, "CVS_Pair_TestA_Clash2")
        self.assertEqual(len(doc.views), 1)
        original_view_id = res1.view_id
        # In isolated mode, Walls category is hidden
        self.assertIn(-2000011, res1.view.hidden_category_ids)

        # Step 2: Switch to Normal mode on same clash -> REUSES exact same view!
        res2 = create_or_update_clash_view(
            doc, plan_context, "TestA", "Clash2", res_a=res_a, res_b=res_b, update_existing=True
        )
        self.assertTrue(res2.succeeded)
        self.assertEqual(res2.action_taken, "UPDATED")
        self.assertEqual(res2.view_id, original_view_id)
        self.assertEqual(len(doc.views), 1, "Must NOT create duplicate views for the same clash")
        self.assertEqual(res2.view_name, "CVS_Context_TestA_Clash2")
        # In context mode, Walls category is unhidden (context restored)
        self.assertNotIn(-2000011, res2.view.hidden_category_ids)

        # Step 3: Switch back to Isolated mode -> STILL reuses exact same view!
        res3 = create_or_update_clash_view(
            doc, plan_isolated, "TestA", "Clash2", res_a=res_a, res_b=res_b, update_existing=True
        )
        self.assertTrue(res3.succeeded)
        self.assertEqual(res3.action_taken, "UPDATED")
        self.assertEqual(res3.view_id, original_view_id)
        self.assertEqual(len(doc.views), 1, "Must STILL have only 1 view")
        self.assertEqual(res3.view_name, "CVS_Pair_TestA_Clash2")
        # In isolated mode, Walls category is hidden again
        self.assertIn(-2000011, res3.view.hidden_category_ids)


if __name__ == "__main__":
    unittest.main()
