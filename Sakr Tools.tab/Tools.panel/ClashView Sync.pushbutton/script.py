# -*- coding: utf-8 -*-
"""ClashView Sync - Streamlined 3D Clash View Generator.

Creates a dedicated 3D section box view for any clash from your Navisworks report.
Only 2 simple options:
  1. Normal 3D Crop Box (on the 2 items) - context section box with surrounding elements visible.
  2. Isolated 3D Crop Box - section box focused on the 2 items with other elements isolated/hidden.

Brand: Deep Plum #4C2335 | Warm Terracotta #E07856 | Warm Cream #F5F0E8 | Soft Black #3B332E
Author: Hazem Sakr | BIM Automation & Development
LinkedIn: https://www.linkedin.com/in/hazem-sakr/

Target: Revit 2024.3 | pyRevit | IronPython 2.7.12
"""
from __future__ import absolute_import, division, print_function

import os
import re
import sys

AUTHOR_NAME = "Hazem Sakr | BIM Automation & Development"
AUTHOR_URL = "https://www.linkedin.com/in/hazem-sakr/"

_EXT_LIB = os.path.abspath(
    os.path.join(os.path.dirname(__file__), r"..\..\..\lib")
)
if os.path.isdir(_EXT_LIB) and _EXT_LIB not in sys.path:
    sys.path.insert(0, _EXT_LIB)

# Purge any cached clashview_sync modules to guarantee live reload
for _mod in list(sys.modules.keys()):
    if "clashview_sync" in _mod:
        del sys.modules[_mod]

from pyrevit import forms, revit, script

try:
    from clashview_sync import (
        ClashPoint,
        ElementResolver,
        calculate_clash_framing,
        convert_length,
        create_or_update_clash_view,
        evaluate_clash_geometry_corroboration,
        extract_element_host_bbox,
        parse_clash_xml,
    )
    HAS_CLASHVIEW_SYNC = True
except ImportError as err:
    HAS_CLASHVIEW_SYNC = False
    _IMPORT_ERROR = str(err)

CONFIG_SECTION = "clashview_sync"

OPT_NORMAL = "Normal 3D Crop Box (on the 2 items)"
OPT_ISOLATED = "Isolated 3D Crop Box"


_MAIN_XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="ClashView Sync" Width="520" Height="470"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F5F0E8" Topmost="True" ShowInTaskbar="False">
    <Window.Resources>
        <Style x:Key="ActionBtn" TargetType="Button">
            <Setter Property="Height" Value="42"/>
            <Setter Property="Margin" Value="0,0,0,10"/>
            <Setter Property="FontSize" Value="13"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
            <Setter Property="Foreground" Value="#F5F0E8"/>
            <Setter Property="Background" Value="#4C2335"/>
            <Setter Property="BorderThickness" Value="0"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border x:Name="bd" Background="{TemplateBinding Background}" CornerRadius="6">
                            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="bd" Property="Background" Value="#E07856"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style x:Key="SmallBtn" TargetType="Button">
            <Setter Property="Height" Value="26"/>
            <Setter Property="FontSize" Value="11"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
            <Setter Property="Foreground" Value="#F5F0E8"/>
            <Setter Property="Background" Value="#4C2335"/>
            <Setter Property="BorderThickness" Value="0"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Padding" Value="10,0"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border x:Name="bd" Background="{TemplateBinding Background}" CornerRadius="4">
                            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="bd" Property="Background" Value="#E07856"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style x:Key="AuthorLinkBtn" TargetType="Button">
            <Setter Property="Background" Value="Transparent"/>
            <Setter Property="BorderThickness" Value="0"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Padding" Value="6,4"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border x:Name="bd" Background="Transparent" CornerRadius="4" Padding="{TemplateBinding Padding}">
                            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="bd" Property="Background" Value="#E6DFD5"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
    </Window.Resources>
    <Grid Margin="24,16,24,14">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
        </Grid.RowDefinitions>

        <!-- Header -->
        <TextBlock Grid.Row="0" Text="ClashView Sync" FontSize="19" FontWeight="Bold"
                   Foreground="#4C2335" Margin="0,0,0,2"/>
        <Rectangle Grid.Row="1" Height="2" Fill="#E07856" Margin="0,0,0,12"/>

        <!-- XML Report Section -->
        <Border Grid.Row="2" Background="#ECE4D8" CornerRadius="6" Padding="10,8" Margin="0,0,0,12">
            <DockPanel LastChildFill="True">
                <Button x:Name="btn_change_xml" DockPanel.Dock="Right" Style="{StaticResource SmallBtn}"
                        Content="Change XML..."/>
                <StackPanel VerticalAlignment="Center" Margin="0,0,10,0">
                    <TextBlock Text="REPORT XML" FontSize="9" FontWeight="Bold" Foreground="#4C2335" Opacity="0.7"/>
                    <TextBlock x:Name="txt_xml_file" Text="Loading..." FontSize="12" FontWeight="SemiBold"
                               Foreground="#3B332E" TextTrimming="CharacterEllipsis"/>
                </StackPanel>
            </DockPanel>
        </Border>

        <!-- Test Selector & Clash Selector -->
        <StackPanel Grid.Row="3" Margin="0,0,0,10">
            <TextBlock Text="Clash Test:" FontSize="12" FontWeight="Bold" Foreground="#4C2335" Margin="2,0,0,4"/>
            <ComboBox x:Name="cbo_test" Height="30" FontSize="12" Foreground="#3B332E" Margin="0,0,0,10"
                      Background="#FFFFFF"/>

            <TextBlock Text="Clash Name (choose from list or type):" FontSize="12" FontWeight="Bold"
                       Foreground="#4C2335" Margin="2,0,0,4"/>
            <ComboBox x:Name="cbo_clash" Height="32" FontSize="13" FontWeight="SemiBold"
                      Foreground="#3B332E" IsEditable="True" Background="#FFFFFF"/>
        </StackPanel>

        <!-- Highlighting Option -->
        <StackPanel Grid.Row="4" Margin="2,0,0,12">
            <CheckBox x:Name="chk_highlight" Content="Highlight clashing elements (Navisworks Red / Green)"
                      FontSize="12" FontWeight="SemiBold" Foreground="#3B332E"
                      IsChecked="True" Cursor="Hand"/>
        </StackPanel>

        <!-- Action Mode Buttons -->
        <StackPanel Grid.Row="5">
            <Button x:Name="btn_normal" Style="{StaticResource ActionBtn}"
                    Content="Normal 3D Crop Box (on the 2 items)"/>
            <Button x:Name="btn_isolated" Style="{StaticResource ActionBtn}"
                    Content="Isolated 3D Crop Box"/>
        </StackPanel>

        <!-- Footer / Clickable LinkedIn Signature -->
        <Button x:Name="btn_author" Grid.Row="6" Style="{StaticResource AuthorLinkBtn}"
                HorizontalAlignment="Center" VerticalAlignment="Bottom"
                ToolTip="Connect with Hazem Sakr on LinkedIn (https://www.linkedin.com/in/hazem-sakr/)">
            <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
                <Border Background="#0A66C2" CornerRadius="2" Width="14" Height="14" Margin="0,0,6,0" VerticalAlignment="Center">
                    <TextBlock Text="in" Foreground="#FFFFFF" FontSize="9" FontWeight="Bold"
                               HorizontalAlignment="Center" VerticalAlignment="Center" Margin="0,-1,0,0"/>
                </Border>
                <TextBlock Text="Hazem Sakr" FontSize="11" FontWeight="Bold" Foreground="#4C2335"
                           TextDecorations="Underline" VerticalAlignment="Center"/>
                <TextBlock Text=" | BIM Automation &amp; Development" FontSize="11" Foreground="#3B332E"
                           VerticalAlignment="Center"/>
            </StackPanel>
        </Button>
    </Grid>
</Window>
"""


class ClashViewSyncWindow(forms.WPFWindow):
    """Unified ClashView Sync window with XML switcher, test dropdown, clash picker, and mode actions."""

    def __init__(self, xml_path, payload, config):
        forms.WPFWindow.__init__(self, _MAIN_XAML, literal_string=True)
        self.xml_path = xml_path
        self.payload = payload
        self.config = config
        self.choice = None
        self.selected_test = None
        self.selected_clash_text = ""
        self.highlight_enabled = True

        # Wire up event handlers
        self.btn_change_xml.Click += self._on_change_xml
        self.cbo_test.SelectionChanged += self._on_test_changed
        self.btn_normal.Click += self._on_normal
        self.btn_isolated.Click += self._on_isolated
        self.btn_author.Click += self._on_author_click

        # Set highlight checkbox state from config
        saved_hl = getattr(config, "highlight_elements", True)
        if saved_hl is None:
            saved_hl = True
        self.chk_highlight.IsChecked = bool(saved_hl)

        # Initialize XML display and Test/Clash items
        self._refresh_xml_ui()

    def _refresh_xml_ui(self):
        if self.xml_path and os.path.isfile(self.xml_path):
            base_name = os.path.basename(self.xml_path)
            self.txt_xml_file.Text = base_name
            self.txt_xml_file.ToolTip = self.xml_path
        else:
            self.txt_xml_file.Text = "No XML selected"
            self.txt_xml_file.ToolTip = ""

        # Populate Tests
        self.cbo_test.Items.Clear()
        if not self.payload or not getattr(self.payload, "tests", None):
            self.cbo_clash.Items.Clear()
            return

        last_test_name = getattr(self.config, "last_test", "") or ""
        selected_idx = 0
        for i, t in enumerate(self.payload.tests):
            label = "{0} ({1} clashes)".format(t.name, len(t.results))
            self.cbo_test.Items.Add(label)
            if last_test_name and _norm(t.name) == _norm(last_test_name):
                selected_idx = i

        if self.cbo_test.Items.Count > 0:
            self.cbo_test.SelectedIndex = selected_idx
            self._update_clashes_for_selected_test()

    def _on_test_changed(self, sender, args):
        self._update_clashes_for_selected_test()

    def _update_clashes_for_selected_test(self):
        idx = self.cbo_test.SelectedIndex
        if idx < 0 or not self.payload or idx >= len(self.payload.tests):
            self.cbo_clash.Items.Clear()
            self.selected_test = None
            return

        test = self.payload.tests[idx]
        self.selected_test = test

        self.cbo_clash.Items.Clear()
        last_clash = getattr(self.config, "last_clash", "") or ""
        match_idx = -1

        for i, c in enumerate(test.results):
            self.cbo_clash.Items.Add(c.name)
            if last_clash and _norm(c.name) == _norm(last_clash):
                match_idx = i

        if match_idx >= 0:
            self.cbo_clash.SelectedIndex = match_idx
        elif self.cbo_clash.Items.Count > 0:
            self.cbo_clash.SelectedIndex = 0
        else:
            self.cbo_clash.Text = ""

    def _on_change_xml(self, sender, args):
        init_dir = (
            os.path.dirname(self.xml_path)
            if self.xml_path and os.path.isdir(os.path.dirname(self.xml_path))
            else ""
        )
        new_path = forms.pick_file(
            file_ext="xml",
            files_filter="Navisworks Clash Report XML (*.xml)|*.xml|All Files (*.*)|*.*",
            title="Select Navisworks Clash Report XML",
            init_dir=init_dir,
        )
        if not new_path:
            return

        try:
            new_payload = parse_clash_xml(new_path)
            total = sum(len(t.results) for t in new_payload.tests)
            if total == 0:
                forms.alert(
                    "This XML file contains 0 clash results.\n\n"
                    "Make sure you exported from Navisworks Clash Detective > Report > Write Report (XML).",
                    title="ClashView Sync",
                )
                return

            self.xml_path = new_path
            self.payload = new_payload
            self.config.last_xml = new_path
            script.save_config()
            self._refresh_xml_ui()
        except Exception as ex:
            forms.alert(
                "Could not parse XML report:\n\n{0}".format(ex),
                title="ClashView Sync",
            )

    def _on_normal(self, sender, args):
        self._submit(OPT_NORMAL)

    def _on_isolated(self, sender, args):
        self._submit(OPT_ISOLATED)

    def _submit(self, choice):
        clash_text = str(self.cbo_clash.Text or self.cbo_clash.SelectedItem or "").strip()
        if not clash_text:
            forms.alert(
                "Please select or type a clash name (e.g. Clash2).",
                title="ClashView Sync",
            )
            return

        self.choice = choice
        self.selected_clash_text = clash_text
        self.highlight_enabled = bool(self.chk_highlight.IsChecked)

        if self.selected_test:
            self.config.last_test = self.selected_test.name
        self.config.last_clash = clash_text
        self.config.highlight_elements = self.highlight_enabled
        if self.xml_path:
            self.config.last_xml = self.xml_path
        script.save_config()

        self.Close()

    def _on_author_click(self, sender, args):
        """Opens Hazem Sakr's LinkedIn profile in the default web browser."""
        import webbrowser
        try:
            webbrowser.open(AUTHOR_URL)
        except Exception:
            try:
                import System.Diagnostics.Process as Process
                Process.Start(AUTHOR_URL)
            except Exception:
                pass


def _norm(text):
    """Case/space-insensitive key for clash name matching."""
    return re.sub(r"[\s_\-]+", "", "{0}".format(text or "")).lower()


def _get_xml_path(config, force_pick):
    """Returns the remembered XML path, asking only when missing or when SHIFT is held."""
    last = getattr(config, "last_xml", None)
    if last and os.path.isfile(last) and not force_pick:
        return last
    path = forms.pick_file(
        file_ext="xml",
        files_filter="Navisworks Clash Report XML (*.xml)|*.xml|All Files (*.*)|*.*",
        title="Select Navisworks Clash Detective REPORT XML",
        init_dir=os.path.dirname(last) if last and os.path.isdir(os.path.dirname(last)) else "",
    )
    if path:
        config.last_xml = path
        script.save_config()
    return path


def _find_matches_in_test(test, query):
    """Finds clash results within a specific test matching query."""
    q = _norm(query)
    if not q or not test:
        return []
    candidates = [q]
    if q.isdigit():
        candidates.append("clash" + q)

    found = []
    for clash in test.results:
        n = _norm(clash.name)
        if n in candidates:
            found.append((test, clash))
    return found


def _find_matches(payload, query):
    """Finds clash results matching a typed name across all tests in payload. Returns list of (test, clash)."""
    q = _norm(query)
    if not q:
        return []
    candidates = [q]
    if q.isdigit():
        candidates.append("clash" + q)

    found = []
    for test in payload.tests:
        for clash in test.results:
            n = _norm(clash.name)
            if n in candidates:
                found.append((test, clash))
    return found


def _disambiguate(query, matches):
    """Only shown when the same clash name exists in several tests."""
    labels = ["{0}  >  {1}".format(t.name, c.name) for t, c in matches]
    chosen = forms.SelectFromList.show(
        labels,
        title="'{0}' exists in {1} tests - pick one".format(query, len(matches)),
        button_name="Select Test",
        multiselect=False,
        width=700,
        height=350,
    )
    if not chosen:
        return None
    return matches[labels.index(chosen)]


def _build_clash_view(doc, resolver, test, clash, xml_path, mode, highlight_elements=True):
    """Resolves the clash and creates the requested 3D clash view. Returns (result, notes, res_a, res_b)."""
    notes = []
    res_a, res_b = resolver.resolve_clash(clash)

    bbox_a = bbox_b = None
    if res_a.is_resolved and res_a.element is not None:
        bbox_a, d = extract_element_host_bbox(res_a.element, link_instance=res_a.link_instance)
        notes.extend(d)
    if res_b.is_resolved and res_b.element is not None:
        bbox_b, d = extract_element_host_bbox(res_b.element, link_instance=res_b.link_instance)
        notes.extend(d)

    clash_pt_ft = None
    if clash.clash_point is not None:
        cp = clash.clash_point
        try:
            clash_pt_ft = ClashPoint(
                convert_length(cp.x, cp.units, "ft"),
                convert_length(cp.y, cp.units, "ft"),
                convert_length(cp.z, cp.units, "ft"),
                "ft",
            )
        except Exception as ex:
            notes.append("Clash point unit conversion failed: {0}".format(ex))

    evaluate_clash_geometry_corroboration(bbox_a, bbox_b, clash_point_ft=clash_pt_ft)

    pair_margin = 1.0 if mode == "pair" else 2.0
    min_span = 4.0 if mode == "pair" else 6.0
    max_span = 80.0

    plan = calculate_clash_framing(
        res_a,
        res_b,
        mode=mode,
        pair_margin_ft=pair_margin,
        context_margin_ft=pair_margin,
        min_box_span_ft=min_span,
        max_box_span_ft=max_span,
        bbox_a=bbox_a,
        bbox_b=bbox_b,
        clash_point_ft=clash_pt_ft,
    )
    result = create_or_update_clash_view(
        doc,
        plan,
        test.name,
        clash.name,
        res_a=res_a,
        res_b=res_b,
        clash_guid=clash.guid,
        source_file=os.path.basename(xml_path),
        update_existing=True,
        allow_single_participant_fallback=True,
        highlight_elements=highlight_elements,
    )
    notes.insert(
        0,
        "A: {0} ({1}) | B: {2} ({3})".format(
            res_a.status, res_a.target_type, res_b.status, res_b.target_type
        ),
    )
    return result, notes, res_a, res_b


def _select_clashing_participants(uidoc, res_a, res_b):
    """Selects the clashing element(s) in Revit's active selection.

    Supports selecting both Host elements and individual elements inside Revit Link instances
    via Reference.CreateLinkReference(link_inst) without selecting the entire link instance.
    """
    if uidoc is None:
        return

    try:
        import Autodesk.Revit.DB as DB
        import System.Collections.Generic as Generic

        # 1. Try Reference-based selection (targets exact host and linked elements)
        refs = Generic.List[DB.Reference]()
        for r in (res_a, res_b):
            if r is None or not getattr(r, "is_resolved", False):
                continue
            elem = getattr(r, "element", None)
            if elem is None:
                continue
            try:
                base_ref = DB.Reference(elem)
                if getattr(r, "is_linked", False):
                    link_inst = getattr(r, "link_instance", None)
                    if link_inst is not None and hasattr(base_ref, "CreateLinkReference"):
                        link_ref = base_ref.CreateLinkReference(link_inst)
                        if link_ref is not None:
                            refs.Add(link_ref)
                else:
                    refs.Add(base_ref)
            except Exception:
                pass

        if refs.Count > 0 and hasattr(uidoc.Selection, "SetReferences"):
            try:
                uidoc.Selection.SetReferences(refs)
                return
            except Exception:
                pass

        # 2. Fallback to ElementId selection for host elements
        host_ids = Generic.List[DB.ElementId]()
        for r in (res_a, res_b):
            if r is None or not getattr(r, "is_resolved", False):
                continue
            if not getattr(r, "is_linked", False):
                eid = getattr(r, "resolved_element_id", None)
                if eid is not None:
                    try:
                        host_ids.Add(DB.ElementId(int(eid)))
                    except Exception:
                        pass

        if host_ids.Count > 0 and hasattr(uidoc.Selection, "SetElementIds"):
            try:
                uidoc.Selection.SetElementIds(host_ids)
            except Exception:
                pass
    except Exception:
        pass


def _find_unrelated_linked_elements_in_section_box(view, res_a, res_b):
    """Finds non-clashing elements from participating linked models that fall inside view's section box.

    Returns a list of DB.Reference objects created via Reference.CreateLinkReference(link_inst).
    """
    try:
        import Autodesk.Revit.DB as DB
    except Exception:
        return []

    unrelated_refs = []
    if view is None or not hasattr(view, "GetSectionBox"):
        return unrelated_refs

    try:
        sbox = view.GetSectionBox()
    except Exception:
        sbox = None
    if sbox is None:
        return unrelated_refs

    # Collect the 8 corners of the section box in host world coordinates
    s_min = sbox.Min
    s_max = sbox.Max
    local_corners = [
        DB.XYZ(s_min.X, s_min.Y, s_min.Z),
        DB.XYZ(s_min.X, s_min.Y, s_max.Z),
        DB.XYZ(s_min.X, s_max.Y, s_min.Z),
        DB.XYZ(s_min.X, s_max.Y, s_max.Z),
        DB.XYZ(s_max.X, s_min.Y, s_min.Z),
        DB.XYZ(s_max.X, s_min.Y, s_max.Z),
        DB.XYZ(s_max.X, s_max.Y, s_min.Z),
        DB.XYZ(s_max.X, s_max.Y, s_max.Z),
    ]
    s_tf = getattr(sbox, "Transform", None)
    if s_tf is not None and not getattr(s_tf, "IsIdentity", True):
        host_corners = [s_tf.OfPoint(pt) for pt in local_corners]
    else:
        host_corners = local_corners

    for res in (res_a, res_b):
        if res is None or not getattr(res, "is_resolved", False) or not getattr(res, "is_linked", False):
            continue

        link_inst = getattr(res, "link_instance", None)
        if link_inst is None:
            continue

        linked_doc = None
        try:
            linked_doc = link_inst.GetLinkDocument()
        except Exception:
            pass
        if linked_doc is None:
            continue

        clashing_id = getattr(res, "resolved_element_id", None)
        if clashing_id is None:
            continue
        clashing_id = int(clashing_id)

        clashing_elem = getattr(res, "element", None)
        if clashing_elem is None:
            try:
                clashing_elem = linked_doc.GetElement(DB.ElementId(clashing_id))
            except Exception:
                pass
        if clashing_elem is None:
            continue

        cat = getattr(clashing_elem, "Category", None)
        if cat is None or getattr(cat, "Id", None) is None:
            continue
        cat_id = cat.Id

        # Transform host corners into linked document coordinates
        transform = link_inst.GetTotalTransform()
        inv_transform = transform.Inverse
        tx_pts = [inv_transform.OfPoint(pt) for pt in host_corners]

        min_x = min(pt.X for pt in tx_pts)
        min_y = min(pt.Y for pt in tx_pts)
        min_z = min(pt.Z for pt in tx_pts)
        max_x = max(pt.X for pt in tx_pts)
        max_y = max(pt.Y for pt in tx_pts)
        max_z = max(pt.Z for pt in tx_pts)

        buf = 1.0
        outline = DB.Outline(
            DB.XYZ(min_x - buf, min_y - buf, min_z - buf),
            DB.XYZ(max_x + buf, max_y + buf, max_z + buf),
        )
        bbox_filter = DB.BoundingBoxIntersectsFilter(outline)

        try:
            collector = (
                DB.FilteredElementCollector(linked_doc)
                .OfCategoryId(cat_id)
                .WherePasses(bbox_filter)
                .WhereElementIsNotElementType()
            )
            for elem in collector:
                eid_val = elem.Id.IntegerValue if hasattr(elem.Id, "IntegerValue") else elem.Id.Value
                if int(eid_val) != clashing_id:
                    try:
                        base_ref = DB.Reference(elem)
                        if hasattr(base_ref, "CreateLinkReference"):
                            link_ref = base_ref.CreateLinkReference(link_inst)
                            if link_ref is not None:
                                unrelated_refs.append(link_ref)
                    except Exception:
                        pass
        except Exception:
            pass

    return unrelated_refs


def _hide_unrelated_linked_elements(uidoc, view, res_a, res_b):
    """Collects non-clashing linked elements within view's section box and executes UI HideElements."""
    if uidoc is None or view is None:
        return False

    try:
        import Autodesk.Revit.DB as DB
        import Autodesk.Revit.UI as UI
        import System.Collections.Generic as Generic

        unrelated_refs = _find_unrelated_linked_elements_in_section_box(view, res_a, res_b)
        if not unrelated_refs:
            return False

        ref_list = Generic.List[DB.Reference]()
        for r in unrelated_refs:
            ref_list.Add(r)

        uidoc.Selection.SetReferences(ref_list)

        hide_cmd = UI.RevitCommandId.LookupPostableCommandId(UI.PostableCommand.HideElements)
        if hide_cmd is not None:
            ui_app = getattr(uidoc, "Application", None)
            if ui_app is not None:
                try:
                    ui_app.PostCommand(hide_cmd)
                    return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


def main():
    if not HAS_CLASHVIEW_SYNC:
        forms.alert(
            "Failed to load clashview_sync package:\n\n{0}".format(_IMPORT_ERROR),
            title="ClashView Sync",
            exitscript=True,
        )

    doc = revit.doc
    if not doc:
        forms.alert("Open a Revit project first.", title="ClashView Sync", exitscript=True)

    config = script.get_config(CONFIG_SECTION)
    force_pick = __shiftclick__ if "__shiftclick__" in globals() else False

    xml_path = _get_xml_path(config, force_pick)
    if not xml_path:
        script.exit()

    try:
        payload = parse_clash_xml(xml_path)
    except Exception as ex:
        forms.alert("Could not read XML:\n\n{0}".format(ex), title="ClashView Sync", exitscript=True)

    total = sum(len(t.results) for t in payload.tests)
    if total == 0:
        forms.alert(
            "This XML has 0 clash results.\n\n"
            "It looks like exported test SETTINGS, not a clash report.\n"
            "In Navisworks: Clash Detective > Report tab > Format: XML > Write Report.",
            title="ClashView Sync - no clashes in file",
        )
        config.last_xml = ""
        script.save_config()
        script.exit()

    # Show Unified ClashView Sync Window
    win = ClashViewSyncWindow(xml_path, payload, config)
    win.ShowDialog()

    if not win.choice:
        script.exit()

    mode = "context" if win.choice == OPT_NORMAL else "pair"
    highlight_enabled = win.highlight_enabled
    selected_test = win.selected_test

    resolver = ElementResolver(doc)
    queries = [q.strip() for q in win.selected_clash_text.split(",") if q.strip()]
    first_view = None
    first_res = None
    errors = []

    for query in queries:
        matches = []
        if selected_test:
            matches = _find_matches_in_test(selected_test, query)
        if not matches:
            matches = _find_matches(win.payload, query)

        if not matches:
            errors.append("No clash named '{0}' found in {1}.".format(
                query, selected_test.name if selected_test else "the report"
            ))
            continue

        if len(matches) > 1:
            picked = _disambiguate(query, matches)
            if not picked:
                continue
            matches = [picked]

        test, clash = matches[0]
        result, notes, res_a, res_b = _build_clash_view(
            doc,
            resolver,
            test,
            clash,
            win.xml_path,
            mode=mode,
            highlight_elements=highlight_enabled,
        )

        if result.succeeded and result.view is not None:
            if first_view is None:
                first_view = result.view
                first_res = (res_a, res_b)
        else:
            reason = "\n".join(result.diagnostics[-3:]) if result.diagnostics else "Unknown error"
            errors.append("{0} failed:\n{1}".format(query, reason))

    # Activate the created/updated view immediately in Revit and select/isolate elements
    if first_view is not None and getattr(revit, "uidoc", None) is not None:
        try:
            revit.uidoc.ActiveView = first_view
        except Exception:
            pass

        hidden_linked = False
        if mode == "pair" and first_res:
            hidden_linked = _hide_unrelated_linked_elements(
                revit.uidoc, first_view, first_res[0], first_res[1]
            )

        if not hidden_linked and first_res:
            try:
                _select_clashing_participants(revit.uidoc, first_res[0], first_res[1])
            except Exception:
                pass

    if errors:
        forms.alert(
            "\n\n".join(errors),
            title="ClashView Sync - Attention",
            warn_icon=True,
        )


if __name__ == "__main__":
    main()
