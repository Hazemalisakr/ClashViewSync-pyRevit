# -*- coding: utf-8 -*-
"""Link-aware element resolution and diagnostics for ClashView Sync.

Resolves a ClashParticipant against a live or mock Revit document, determining
whether the element resides in the host model, a direct linked model, or a
nested link, and identifying the exact RevitLinkInstance where applicable.

Key principles:
- Strict evidence-based matching: never silently pick the first element when ambiguous.
- Distinct handling of host elements, direct linked elements, multiple link instances,
  unloaded links, and nested links.
- Explicit path and name normalization handling file extensions (.nwc vs .rvt) and URI schemes.
- Corroboration of Category, Name, and UniqueId without treating them as sole identifiers.
- Complete separation of Revit API dependencies for offline testing.

Compatible with Python 2.7 (pyRevit IronPython) and Python 3.x.
"""
from __future__ import absolute_import, division, print_function

import os
import re
import sys

# Safe import of Revit API and CLR System assemblies
try:
    import System
except ImportError:
    System = None

try:
    import Autodesk.Revit.DB as DB
    HAS_REVIT_API = True
except ImportError:
    DB = None
    HAS_REVIT_API = False

from .models import integer_types, string_types

# Known CAD/BIM model file extensions to strip during base name normalization
MODEL_EXTENSIONS = (
    ".nwc",
    ".rvt",
    ".ifc",
    ".dwg",
    ".dgn",
    ".nwd",
    ".nwf",
    ".skp",
)


class ResolutionStatus(object):
    """Enumeration of possible element resolution outcomes."""

    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class TargetDocumentType(object):
    """Enumeration of target document placement contexts."""

    HOST = "HOST"
    LINK = "LINK"
    NESTED_LINK = "NESTED_LINK"
    UNKNOWN = "UNKNOWN"


class ConfidenceLevel(object):
    """Confidence rating for the resolution result."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


def get_element_id_value(element_id):
    """Safely extracts Python integer value from a Revit ElementId, System.Int64, mock, or int.

    Always returns a native Python int/long so IronPython 2.7 never leaks a raw
    .NET System.Int64 that fails isinstance(..., integer_types) or json.dumps().
    """
    if element_id is None:
        return None
    if isinstance(element_id, bool):
        return None
    if isinstance(element_id, integer_types):
        return int(element_id)
    if hasattr(element_id, "Value"):
        # Revit 2024+ 64-bit ElementId (.Value is System.Int64 in CLR)
        try:
            return int(element_id.Value)
        except (ValueError, TypeError):
            return None
    if hasattr(element_id, "IntegerValue"):
        # Revit 2023 and older 32-bit ElementId
        try:
            return int(element_id.IntegerValue)
        except (ValueError, TypeError):
            return None
    try:
        return int(element_id)
    except (ValueError, TypeError):
        return None


def create_revit_element_id(int_val):
    """Safely constructs a Revit 2024+ DB.ElementId(System.Int64) or returns Python int in offline mode."""
    if int_val is None:
        return None
    clean_int = get_element_id_value(int_val)
    if clean_int is None:
        return None

    if HAS_REVIT_API and DB is not None:
        if System is not None and hasattr(System, "Int64"):
            try:
                return DB.ElementId(System.Int64(clean_int))
            except Exception:
                pass
        return DB.ElementId(int(clean_int))
    return clean_int


def normalize_path_or_uri(path_str):
    """Normalizes path separators, URI schemes, and casing."""
    if not path_str or not isinstance(path_str, string_types):
        return ""
    p = path_str.strip()
    # Replace backslashes with forward slashes
    p = p.replace("\\", "/")
    # Strip file URI prefix
    if p.lower().startswith("file:///"):
        p = p[8:]
    elif p.lower().startswith("file://"):
        p = p[7:]
    return p


def extract_base_model_name(name_or_path):
    """Extracts a normalized, lowercase base model name without extensions or instance suffixes.

    Examples:
      'C:/Models/MEP_Services.nwc' -> 'mep_services'
      'MEP_Services.rvt : 2 : location <Not Shared>' -> 'mep_services'
      '360-JDF02-ARC.rvt' -> '360-jdf02-arc'
      'Autodesk Docs://Project/Arch.rvt' -> 'arch'
    """
    if not name_or_path or not isinstance(name_or_path, string_types):
        return ""

    raw = normalize_path_or_uri(name_or_path)

    # Strip Revit link instance suffix e.g. ' : 1 : location <Not Shared>' or ' : 2'
    raw = re.sub(r"\s*:\s*\d+\s*(:.*)?$", "", raw)

    # Get file basename
    base = os.path.basename(raw) if "/" in raw else raw

    # Strip known extensions iteratively
    base_lower = base.lower()
    for ext in MODEL_EXTENSIONS:
        if base_lower.endswith(ext):
            base_lower = base_lower[: -len(ext)]
            break

    return base_lower.strip()


def parse_instance_index_from_name(name_str):
    """Extracts instance index integer if present in link name (e.g. ': 2 :' -> 2)."""
    if not name_str or not isinstance(name_str, string_types):
        return None
    match = re.search(r":\s*(\d+)\s*(:|$)", name_str)
    if match:
        try:
            return int(match.group(1))
        except (ValueError, TypeError):
            return None
    return None


class ParticipantResolution(object):
    """Structured result of resolving a ClashParticipant against Revit documents."""

    def __init__(
        self,
        participant,
        status=ResolutionStatus.UNRESOLVED,
        target_type=TargetDocumentType.UNKNOWN,
        confidence=ConfidenceLevel.NONE,
        host_element=None,
        linked_element=None,
        link_instance=None,
        linked_document=None,
        match_strategy=None,
        candidates=None,
        diagnostics=None,
        corroboration=None,
    ):
        self.participant = participant
        self.status = status
        self.target_type = target_type
        self.confidence = confidence

        # Resolved Revit element references
        self.host_element = host_element
        self.linked_element = linked_element
        self.link_instance = link_instance
        self.linked_document = linked_document

        self.match_strategy = match_strategy or "NONE"
        self.candidates = list(candidates) if candidates else []
        self.diagnostics = list(diagnostics) if diagnostics else []
        self.corroboration = dict(corroboration) if corroboration else {}

    @property
    def is_resolved(self):
        """Returns True if the element was successfully and unambiguously resolved."""
        return self.status == ResolutionStatus.RESOLVED

    @property
    def is_linked(self):
        """Returns True if the resolved element resides inside a linked document."""
        return self.target_type in (
            TargetDocumentType.LINK,
            TargetDocumentType.NESTED_LINK,
        )

    @property
    def resolved_element(self):
        """Convenience property returning either the linked or host element."""
        return self.linked_element if self.linked_element is not None else self.host_element

    @property
    def element(self):
        """Alias for resolved_element."""
        return self.resolved_element

    @property
    def resolved_element_id(self):
        """Returns the Revit ElementId integer of the resolved element if available."""
        elem = self.resolved_element
        if elem is not None and hasattr(elem, "Id"):
            return get_element_id_value(elem.Id)
        return self.participant.element_id if self.participant else None

    @property
    def link_instance_id(self):
        """Returns the host ElementId integer of the link instance if resolved."""
        if self.link_instance is not None and hasattr(self.link_instance, "Id"):
            return get_element_id_value(self.link_instance.Id)
        return None

    def to_dict(self):
        """Serializes resolution summary to a standard dictionary."""
        return {
            "status": self.status,
            "target_type": self.target_type,
            "confidence": self.confidence,
            "is_resolved": self.is_resolved,
            "is_linked": self.is_linked,
            "match_strategy": self.match_strategy,
            "participant_id": self.participant.element_id if self.participant else None,
            "participant_model": self.participant.model_name if self.participant else None,
            "resolved_element_id": self.resolved_element_id,
            "link_instance_id": self.link_instance_id,
            "candidate_count": len(self.candidates),
            "candidates": self.candidates,
            "diagnostics": self.diagnostics,
            "corroboration": self.corroboration,
        }

    def __repr__(self):
        return (
            "ParticipantResolution(status='{0}', target='{1}', id={2}, "
            "link_inst={3}, conf='{4}')".format(
                self.status,
                self.target_type,
                self.resolved_element_id,
                self.link_instance_id,
                self.confidence,
            )
        )


class LinkInstanceDescriptor(object):
    """Metadata wrapper around a RevitLinkInstance to enable uniform querying."""

    def __init__(self, link_instance, host_doc=None):
        self.instance = link_instance
        self.host_doc = host_doc

        # Instance ID
        self.instance_id = (
            get_element_id_value(link_instance.Id)
            if hasattr(link_instance, "Id")
            else None
        )

        # Instance Name (e.g. 'Model.rvt : 1 : location <Not Shared>')
        self.instance_name = getattr(link_instance, "Name", "") or ""

        # Instance index (e.g. 1)
        self.instance_index = parse_instance_index_from_name(self.instance_name)

        # Link Type details
        self.type_id = None
        self.type_name = ""
        if hasattr(link_instance, "GetTypeId"):
            try:
                self.type_id = get_element_id_value(link_instance.GetTypeId())
                if host_doc and hasattr(host_doc, "GetElement") and self.type_id is not None:
                    # Look up RevitLinkType using safe 64-bit ElementId constructor
                    ltype = host_doc.GetElement(create_revit_element_id(self.type_id))
                    if ltype:
                        self.type_name = getattr(ltype, "Name", "") or ""
            except Exception:
                pass

        # Linked document access
        self.linked_doc = None
        if hasattr(link_instance, "GetLinkDocument"):
            try:
                self.linked_doc = link_instance.GetLinkDocument()
            except Exception:
                self.linked_doc = None

        self.is_loaded = self.linked_doc is not None

        # Document Title and Path
        self.doc_title = getattr(self.linked_doc, "Title", "") if self.linked_doc else ""
        self.doc_path = getattr(self.linked_doc, "PathName", "") if self.linked_doc else ""

        # Fallback to type name if doc unloaded
        if not self.doc_title and self.type_name:
            self.doc_title = self.type_name

        # Normalized base identifiers
        self.normalized_base = extract_base_model_name(
            self.doc_title or self.instance_name
        )
        self.normalized_path = normalize_path_or_uri(self.doc_path).lower()

    def matches_participant_model(self, candidate_names, candidate_paths=None):
        """Evaluates whether this link matches the given candidate names or paths.

        Returns (is_match: bool, match_type: str)
        """
        # 1. Exact path match (highest confidence)
        if candidate_paths and self.normalized_path:
            for p in candidate_paths:
                clean_p = normalize_path_or_uri(p).lower()
                if clean_p and clean_p == self.normalized_path:
                    return (True, "EXACT_PATH_MATCH")

        # 2. Exact instance name match (e.g. node matching 'Model.rvt : 2 : location')
        clean_inst_name = self.instance_name.strip().lower()
        if clean_inst_name:
            for n in candidate_names:
                if n and n.strip().lower() == clean_inst_name:
                    return (True, "EXACT_INSTANCE_NAME_MATCH")

        # 3. Base name match (e.g. 'mep_services' matching 'mep_services')
        if self.normalized_base:
            for n in candidate_names:
                base_cand = extract_base_model_name(n)
                if base_cand and base_cand == self.normalized_base:
                    return (True, "BASE_NAME_MATCH")

        return (False, "NO_MATCH")

    def to_dict(self):
        return {
            "instance_id": self.instance_id,
            "instance_name": self.instance_name,
            "instance_index": self.instance_index,
            "doc_title": self.doc_title,
            "doc_path": self.doc_path,
            "is_loaded": self.is_loaded,
            "normalized_base": self.normalized_base,
        }

    def __repr__(self):
        return "LinkInstanceDescriptor(id={0}, name='{1}', loaded={2})".format(
            self.instance_id, self.instance_name, self.is_loaded
        )


class ElementResolver(object):
    """Link-aware element resolver for Revit documents."""

    def __init__(self, host_doc, link_instances=None):
        """Initializes resolver with a target Revit host document.

        Args:
          host_doc: Autodesk.Revit.DB.Document or duck-typed test object.
          link_instances: optional pre-collected list of RevitLinkInstance objects.
        """
        self.host_doc = host_doc

        # Inspect host document properties
        self.host_title = getattr(host_doc, "Title", "") or ""
        self.host_path = getattr(host_doc, "PathName", "") or ""
        self.host_normalized_base = extract_base_model_name(
            self.host_title or self.host_path
        )
        self.host_normalized_path = normalize_path_or_uri(self.host_path).lower()

        # Collect and index link instances
        self.link_descriptors = self._index_links(link_instances)
        self._nested_link_cache = None

    def _index_links(self, explicit_instances=None):
        """Discovers and wraps all RevitLinkInstances in the host document."""
        instances = []

        if explicit_instances is not None:
            instances = explicit_instances
        elif hasattr(self.host_doc, "link_instances"):
            # Duck-typed test mock
            instances = self.host_doc.link_instances
        elif HAS_REVIT_API and DB is not None and hasattr(self.host_doc, "GetElement"):
            # Real Revit API collector
            try:
                collector = DB.FilteredElementCollector(self.host_doc).OfClass(
                    DB.RevitLinkInstance
                )
                instances = list(collector)
            except Exception:
                instances = []

        descriptors = []
        for inst in instances:
            try:
                desc = LinkInstanceDescriptor(inst, host_doc=self.host_doc)
                descriptors.append(desc)
            except Exception:
                pass
        return descriptors

    def _get_element_safe(self, target_doc, element_id_int):
        """Retrieves element from document by integer ID, handling Revit API vs mocks."""
        if target_doc is None or element_id_int is None or element_id_int <= 0:
            return None

        if not hasattr(target_doc, "GetElement"):
            return None

        try:
            eid = create_revit_element_id(element_id_int)
            return target_doc.GetElement(eid)
        except Exception:
            return None

    def _search_element_across_documents(self, element_id_int, participant):
        """Searches for element_id_int across host document and all loaded link instances.

        Used as a resilient fallback when model_name was not declared or could not be matched
        to document titles (e.g. Navisworks exports where 'Selection Path' was not checked).
        """
        candidates = []
        if element_id_int is None or element_id_int <= 0:
            return candidates

        # 1. Search Host Document
        host_elem = self._get_element_safe(self.host_doc, element_id_int)
        if host_elem is not None:
            corrob = self._corroborate_element(host_elem, participant)
            candidates.append({
                "target_type": TargetDocumentType.HOST,
                "element": host_elem,
                "link_instance": None,
                "linked_document": None,
                "doc_title": self.host_title,
                "corrob": corrob,
            })

        # 2. Search Loaded Link Instances
        for desc in self.link_descriptors:
            if desc.is_loaded and desc.linked_doc is not None:
                link_elem = self._get_element_safe(desc.linked_doc, element_id_int)
                if link_elem is not None:
                    corrob = self._corroborate_element(link_elem, participant)
                    candidates.append({
                        "target_type": TargetDocumentType.LINK,
                        "element": link_elem,
                        "link_instance": desc.instance,
                        "linked_document": desc.linked_doc,
                        "doc_title": desc.doc_title,
                        "instance_id": desc.instance_id,
                        "instance_name": desc.instance_name,
                        "corrob": corrob,
                    })

        return candidates

    def _corroborate_element(self, element, participant):
        """Compares resolved Revit element attributes against clash participant metadata."""
        corroboration = {
            "category_match": None,
            "name_match": None,
            "unique_id_match": None,
            "warnings": [],
        }

        if element is None or participant is None:
            return corroboration

        # 1. Category check
        if participant.category and hasattr(element, "Category") and element.Category:
            revit_cat = getattr(element.Category, "Name", "") or ""
            if revit_cat:
                part_cat = participant.category.strip().lower()
                clean_revit_cat = revit_cat.strip().lower()
                # Direct match or containment (e.g. 'Ducts' in 'OST_DuctCurves' or vice versa)
                if part_cat == clean_revit_cat or part_cat in clean_revit_cat or clean_revit_cat in part_cat:
                    corroboration["category_match"] = True
                else:
                    corroboration["category_match"] = False
                    msg = (
                        "Category mismatch: Revit element category is '{0}', "
                        "but clash participant category is '{1}'."
                    ).format(revit_cat, participant.category)
                    corroboration["warnings"].append(msg)

        # 2. Element Name / Family / Type check
        if participant.item_name and hasattr(element, "Name"):
            revit_name = getattr(element, "Name", "") or ""
            if revit_name:
                p_item = participant.item_name.strip().lower()
                r_item = revit_name.strip().lower()
                if p_item == r_item or p_item in r_item or r_item in p_item:
                    corroboration["name_match"] = True
                else:
                    corroboration["name_match"] = False
                    # Notice rather than fatal error (types/instances often have composite names)
                    msg = (
                        "Name notice: Revit element name is '{0}', "
                        "while clash item name is '{1}'."
                    ).format(revit_name, participant.item_name)
                    corroboration["warnings"].append(msg)

        # 3. UniqueId check (strong corroboration when present)
        if participant.unique_id and hasattr(element, "UniqueId"):
            elem_uid = getattr(element, "UniqueId", "") or ""
            if elem_uid:
                if elem_uid.strip().lower() == participant.unique_id.strip().lower():
                    corroboration["unique_id_match"] = True
                else:
                    corroboration["unique_id_match"] = False
                    msg = (
                        "UniqueId mismatch: Revit element UniqueId '{0}' "
                        "does not match clash UniqueId '{1}'."
                    ).format(elem_uid, participant.unique_id)
                    corroboration["warnings"].append(msg)

        return corroboration

    def _check_host_match(self, candidate_names, candidate_paths):
        """Checks if participant identifiers match the host document."""
        # Exact path match
        if candidate_paths and self.host_normalized_path:
            for p in candidate_paths:
                clean_p = normalize_path_or_uri(p).lower()
                if clean_p and clean_p == self.host_normalized_path:
                    return (True, "EXACT_HOST_PATH_MATCH")

        # Base name match
        if self.host_normalized_base:
            for n in candidate_names:
                base_cand = extract_base_model_name(n)
                if base_cand and base_cand == self.host_normalized_base:
                    return (True, "BASE_HOST_NAME_MATCH")

        return (False, "NO_MATCH")

    def _get_nested_link_descriptors(self):
        """Lazily collects and caches nested link descriptors across loaded parent links."""
        if self._nested_link_cache is not None:
            return self._nested_link_cache

        cached = []
        for desc in self.link_descriptors:
            if not desc.is_loaded or not desc.linked_doc:
                continue

            child_instances = []
            if hasattr(desc.linked_doc, "link_instances"):
                child_instances = desc.linked_doc.link_instances
            elif HAS_REVIT_API and DB is not None and hasattr(desc.linked_doc, "GetElement"):
                try:
                    collector = DB.FilteredElementCollector(desc.linked_doc).OfClass(
                        DB.RevitLinkInstance
                    )
                    child_instances = list(collector)
                except Exception:
                    child_instances = []

            for child in child_instances:
                try:
                    child_desc = LinkInstanceDescriptor(child, host_doc=desc.linked_doc)
                    cached.append((desc, child_desc))
                except Exception:
                    pass

        self._nested_link_cache = cached
        return self._nested_link_cache

    def _check_nested_links(self, candidate_names, candidate_paths):
        """Searches cached nested link descriptors across loaded linked documents."""
        nested_matches = []
        for parent_desc, child_desc in self._get_nested_link_descriptors():
            is_match, match_type = child_desc.matches_participant_model(
                candidate_names, candidate_paths
            )
            if is_match:
                nested_matches.append((parent_desc, child_desc, match_type))

        return nested_matches

    def resolve(self, participant):
        """Resolves a single ClashParticipant against the Revit document.

        Returns:
          ParticipantResolution
        """
        # 1. Guard against missing participant
        if participant is None:
            return ParticipantResolution(
                participant=None,
                status=ResolutionStatus.UNRESOLVED,
                diagnostics=["Participant is None."],
            )

        # 2. Strict ID verification
        if not participant.is_valid_element_id:
            raw_id = participant.element_id_raw
            diag = (
                "Participant has no valid positive integer Element ID (raw='{0}'). "
                "Cannot query Revit element without a valid Element ID."
            ).format(raw_id or "")
            return ParticipantResolution(
                participant=participant,
                status=ResolutionStatus.UNRESOLVED,
                confidence=ConfidenceLevel.NONE,
                diagnostics=[diag] + list(participant.diagnostics),
            )

        elem_id = participant.element_id

        # 3. Gather candidate model identifiers from participant
        candidate_names = []
        if participant.model_name:
            candidate_names.append(participant.model_name)
        if participant.path_nodes:
            # Inspect all selection tree nodes so deep federated NWF/NWD folder hierarchies
            # above the .rvt/.nwc link node still disambiguate RevitLinkInstance names cleanly.
            for node in participant.path_nodes:
                if node and node not in candidate_names:
                    candidate_names.append(node)
        if participant.layer and participant.layer not in candidate_names:
            candidate_names.append(participant.layer)

        candidate_paths = []
        if participant.model_path:
            candidate_paths.append(participant.model_path)
        for k, v in participant.smarttags.items():
            if "path" in k.lower() or "file" in k.lower():
                if v and v not in candidate_paths:
                    candidate_paths.append(v)

        # 4. Evaluate matching link instances
        matching_links = []
        for desc in self.link_descriptors:
            is_match, match_type = desc.matches_participant_model(
                candidate_names, candidate_paths
            )
            if is_match:
                matching_links.append((desc, match_type))

        # 5. Evaluate host document match
        host_matches, host_match_type = self._check_host_match(
            candidate_names, candidate_paths
        )

        # -----------------------------------------------------------------
        # DISAMBIGUATION & ROUTING
        # -----------------------------------------------------------------

        # Case A: Host conflict with link (both match model base name)
        if host_matches and len(matching_links) > 0:
            # Check if participant path explicitly says it's inside a link
            has_link_keyword = any(
                ": " in n or n.lower().endswith(".rvt") for n in candidate_names
            )
            if not has_link_keyword and host_match_type == "EXACT_HOST_PATH_MATCH":
                # Explicit path confirms host
                matching_links = []
            elif not has_link_keyword and host_matches and len(matching_links) > 0:
                diag = (
                    "Ambiguous model match: Participant model '{0}' matches both "
                    "the host document '{1}' and link instance '{2}'."
                ).format(
                    participant.model_name,
                    self.host_title,
                    matching_links[0][0].instance_name,
                )
                return ParticipantResolution(
                    participant=participant,
                    status=ResolutionStatus.AMBIGUOUS,
                    target_type=TargetDocumentType.UNKNOWN,
                    confidence=ConfidenceLevel.LOW,
                    candidates=[desc.to_dict() for desc, _ in matching_links],
                    diagnostics=[diag],
                )

        # Case B: Host element resolution
        if host_matches and len(matching_links) == 0:
            elem = self._get_element_safe(self.host_doc, elem_id)
            if elem is None:
                diag = (
                    "Element ID {0} was not found in host document '{1}'."
                ).format(elem_id, self.host_title)
                return ParticipantResolution(
                    participant=participant,
                    status=ResolutionStatus.UNRESOLVED,
                    target_type=TargetDocumentType.HOST,
                    confidence=ConfidenceLevel.LOW,
                    match_strategy=host_match_type,
                    diagnostics=[diag],
                )

            # Element exists in host: corroborate metadata
            corrob = self._corroborate_element(elem, participant)
            confidence = ConfidenceLevel.HIGH if corrob.get("unique_id_match") else ConfidenceLevel.MEDIUM
            return ParticipantResolution(
                participant=participant,
                status=ResolutionStatus.RESOLVED,
                target_type=TargetDocumentType.HOST,
                confidence=confidence,
                host_element=elem,
                match_strategy=host_match_type,
                corroboration=corrob,
                diagnostics=corrob["warnings"],
            )

        # Case C: Single Link Instance matched
        if len(matching_links) == 1:
            desc, match_type = matching_links[0]

            # Check if link is unloaded
            if not desc.is_loaded or desc.linked_doc is None:
                diag = (
                    "Linked model '{0}' (Instance ID {1}) is currently UNLOADED. "
                    "Element {2} cannot be retrieved until the link is reloaded."
                ).format(desc.instance_name, desc.instance_id, elem_id)
                return ParticipantResolution(
                    participant=participant,
                    status=ResolutionStatus.UNRESOLVED,
                    target_type=TargetDocumentType.LINK,
                    confidence=ConfidenceLevel.MEDIUM,
                    link_instance=desc.instance,
                    match_strategy=match_type,
                    diagnostics=[diag],
                )

            # Query element in linked document
            elem = self._get_element_safe(desc.linked_doc, elem_id)
            if elem is None:
                diag = (
                    "Element ID {0} was not found inside linked document '{1}'."
                ).format(elem_id, desc.doc_title)
                return ParticipantResolution(
                    participant=participant,
                    status=ResolutionStatus.UNRESOLVED,
                    target_type=TargetDocumentType.LINK,
                    confidence=ConfidenceLevel.MEDIUM,
                    link_instance=desc.instance,
                    linked_document=desc.linked_doc,
                    match_strategy=match_type,
                    diagnostics=[diag],
                )

            # Corroborate
            corrob = self._corroborate_element(elem, participant)
            conf = ConfidenceLevel.HIGH if corrob.get("unique_id_match") else ConfidenceLevel.HIGH if match_type in ("EXACT_PATH_MATCH", "EXACT_INSTANCE_NAME_MATCH") else ConfidenceLevel.MEDIUM
            return ParticipantResolution(
                participant=participant,
                status=ResolutionStatus.RESOLVED,
                target_type=TargetDocumentType.LINK,
                confidence=conf,
                linked_element=elem,
                link_instance=desc.instance,
                linked_document=desc.linked_doc,
                match_strategy=match_type,
                corroboration=corrob,
                diagnostics=corrob["warnings"],
            )

        # Case D: Multiple Link Instances matched (e.g. repeated apartment units)
        if len(matching_links) > 1:
            # Attempt disambiguation using instance index or exact instance name in selection hierarchy
            disambiguated = []
            for desc, mtype in matching_links:
                # Check if specific instance name is in candidate names
                if desc.instance_name.strip().lower() in [n.strip().lower() for n in candidate_names]:
                    disambiguated.append((desc, "EXACT_INSTANCE_NAME_DISAMBIGUATION"))
                    continue

                # Check if instance index e.g. ': 2 :' appears in any path node
                if desc.instance_index is not None:
                    idx_pattern = ": {0} :".format(desc.instance_index)
                    if any(idx_pattern in n for n in candidate_names):
                        disambiguated.append((desc, "INSTANCE_INDEX_DISAMBIGUATION"))

            if len(disambiguated) == 1:
                desc, match_type = disambiguated[0]
                # Successfully disambiguated to single instance
                if not desc.is_loaded or desc.linked_doc is None:
                    diag = (
                        "Disambiguated link instance '{0}' is UNLOADED. "
                        "Element {1} cannot be retrieved."
                    ).format(desc.instance_name, elem_id)
                    return ParticipantResolution(
                        participant=participant,
                        status=ResolutionStatus.UNRESOLVED,
                        target_type=TargetDocumentType.LINK,
                        link_instance=desc.instance,
                        match_strategy=match_type,
                        diagnostics=[diag],
                    )

                elem = self._get_element_safe(desc.linked_doc, elem_id)
                if elem is None:
                    diag = (
                        "Element ID {0} not found in disambiguated link '{1}'."
                    ).format(elem_id, desc.doc_title)
                    return ParticipantResolution(
                        participant=participant,
                        status=ResolutionStatus.UNRESOLVED,
                        target_type=TargetDocumentType.LINK,
                        link_instance=desc.instance,
                        linked_document=desc.linked_doc,
                        match_strategy=match_type,
                        diagnostics=[diag],
                    )

                corrob = self._corroborate_element(elem, participant)
                return ParticipantResolution(
                    participant=participant,
                    status=ResolutionStatus.RESOLVED,
                    target_type=TargetDocumentType.LINK,
                    confidence=ConfidenceLevel.HIGH,
                    linked_element=elem,
                    link_instance=desc.instance,
                    linked_document=desc.linked_doc,
                    match_strategy=match_type,
                    corroboration=corrob,
                    diagnostics=corrob["warnings"],
                )

            # Disambiguation failed: multiple identical model placements
            candidate_ids = [desc.instance_id for desc, _ in matching_links]
            diag = (
                "Ambiguous link instance: Model '{0}' matches {1} separate link instances "
                "(Instance IDs: {2}). The clash report does not contain selection tree "
                "hierarchy or instance indices to determine which placement was clashing."
            ).format(
                participant.model_name or "",
                len(matching_links),
                ", ".join(str(cid) for cid in candidate_ids),
            )
            return ParticipantResolution(
                participant=participant,
                status=ResolutionStatus.AMBIGUOUS,
                target_type=TargetDocumentType.LINK,
                confidence=ConfidenceLevel.LOW,
                candidates=[desc.to_dict() for desc, _ in matching_links],
                diagnostics=[diag],
            )

        # Case E: No top-level matches — Check for Nested Links
        nested_matches = self._check_nested_links(candidate_names, candidate_paths)
        if len(nested_matches) == 1:
            parent_desc, child_desc, match_type = nested_matches[0]
            diag = (
                "Unsupported nested link: Element model '{0}' resides inside nested link "
                "'{1}' via parent link '{2}' (Instance ID {3}). The Revit API does not "
                "support direct host 3D view isolation for nested link instances without "
                "parent link context."
            ).format(
                participant.model_name or child_desc.doc_title,
                child_desc.doc_title,
                parent_desc.instance_name,
                parent_desc.instance_id,
            )
            return ParticipantResolution(
                participant=participant,
                status=ResolutionStatus.UNSUPPORTED,
                target_type=TargetDocumentType.NESTED_LINK,
                confidence=ConfidenceLevel.MEDIUM,
                match_strategy="NESTED_LINK_DETECTED",
                candidates=[child_desc.to_dict(), parent_desc.to_dict()],
                diagnostics=[diag],
            )
        elif len(nested_matches) > 1:
            diag = (
                "Ambiguous nested link: Model '{0}' matches {1} nested links across loaded "
                "parent models."
            ).format(participant.model_name or "", len(nested_matches))
            return ParticipantResolution(
                participant=participant,
                status=ResolutionStatus.AMBIGUOUS,
                target_type=TargetDocumentType.NESTED_LINK,
                confidence=ConfidenceLevel.LOW,
                candidates=[c.to_dict() for _, c, _ in nested_matches],
                diagnostics=[diag],
            )

        # Case F: Model name not matched upfront — Attempt Global Element ID Search
        # When Navisworks reports are exported without "Selection Path" (<pathlink>),
        # participant.model_name is None or only a level/layer name ("1ST FLOOR").
        # Search the host document and all loaded link instances directly by Element ID.
        id_candidates = self._search_element_across_documents(elem_id, participant)
        if len(id_candidates) == 1:
            cand = id_candidates[0]
            corrob = cand["corrob"]
            conf = ConfidenceLevel.HIGH if (corrob.get("name_match") or corrob.get("category_match")) else ConfidenceLevel.MEDIUM
            if cand["target_type"] == TargetDocumentType.HOST:
                return ParticipantResolution(
                    participant=participant,
                    status=ResolutionStatus.RESOLVED,
                    target_type=TargetDocumentType.HOST,
                    confidence=conf,
                    host_element=cand["element"],
                    match_strategy="GLOBAL_ID_SEARCH_HOST",
                    corroboration=corrob,
                    diagnostics=[
                        "Resolved via global Element ID search in host document '{0}' (model name was not declared in XML).".format(
                            self.host_title
                        )
                    ] + corrob["warnings"],
                )
            else:
                return ParticipantResolution(
                    participant=participant,
                    status=ResolutionStatus.RESOLVED,
                    target_type=TargetDocumentType.LINK,
                    confidence=conf,
                    linked_element=cand["element"],
                    link_instance=cand["link_instance"],
                    linked_document=cand["linked_document"],
                    match_strategy="GLOBAL_ID_SEARCH_LINK",
                    corroboration=corrob,
                    diagnostics=[
                        "Resolved via global Element ID search in linked model '{0}' (Instance ID {1}) (model name was not declared in XML).".format(
                            cand["instance_name"], cand["instance_id"]
                        )
                    ] + corrob["warnings"],
                )
        elif len(id_candidates) > 1:
            # Multiple loaded documents contain an element with this ID.
            # Try to disambiguate by Category or Item Name corroboration.
            corrob_filtered = [
                c for c in id_candidates
                if c["corrob"].get("category_match") is True or c["corrob"].get("name_match") is True
            ]
            if len(corrob_filtered) == 1:
                cand = corrob_filtered[0]
                corrob = cand["corrob"]
                if cand["target_type"] == TargetDocumentType.HOST:
                    return ParticipantResolution(
                        participant=participant,
                        status=ResolutionStatus.RESOLVED,
                        target_type=TargetDocumentType.HOST,
                        confidence=ConfidenceLevel.HIGH,
                        host_element=cand["element"],
                        match_strategy="GLOBAL_ID_SEARCH_CORROBORATED_HOST",
                        corroboration=corrob,
                        diagnostics=[
                            "Disambiguated via category/name match in host document '{0}' out of {1} ID candidates.".format(
                                self.host_title, len(id_candidates)
                            )
                        ] + corrob["warnings"],
                    )
                else:
                    return ParticipantResolution(
                        participant=participant,
                        status=ResolutionStatus.RESOLVED,
                        target_type=TargetDocumentType.LINK,
                        confidence=ConfidenceLevel.HIGH,
                        linked_element=cand["element"],
                        link_instance=cand["link_instance"],
                        linked_document=cand["linked_document"],
                        match_strategy="GLOBAL_ID_SEARCH_CORROBORATED_LINK",
                        corroboration=corrob,
                        diagnostics=[
                            "Disambiguated via category/name match in linked model '{0}' out of {1} ID candidates.".format(
                                cand["instance_name"], len(id_candidates)
                            )
                        ] + corrob["warnings"],
                    )

            # Truly ambiguous across documents
            cand_dicts = [
                {"doc_title": c["doc_title"], "target_type": c["target_type"], "instance_name": c.get("instance_name", "")}
                for c in id_candidates
            ]
            diag = (
                "Ambiguous Element ID: Element ID {0} was found in {1} loaded documents ({2}), "
                "and the clash report lacks model names/selection tree paths to distinguish them."
            ).format(
                elem_id,
                len(id_candidates),
                ", ".join(c["doc_title"] for c in id_candidates),
            )
            ambig_res = ParticipantResolution(
                participant=participant,
                status=ResolutionStatus.AMBIGUOUS,
                target_type=TargetDocumentType.UNKNOWN,
                confidence=ConfidenceLevel.LOW,
                candidates=cand_dicts,
                diagnostics=[diag],
            )
            ambig_res.candidate_objects = id_candidates
            return ambig_res

        # Case G: Element ID not found in any loaded document
        diag = (
            "Model '{0}' could not be matched to the host document '{1}' or to any of the {2} "
            "loaded link instances, and Element ID {3} was not found in any loaded document."
        ).format(
            participant.model_name or "(Not declared)",
            self.host_title,
            len(self.link_descriptors),
            elem_id,
        )
        return ParticipantResolution(
            participant=participant,
            status=ResolutionStatus.UNRESOLVED,
            target_type=TargetDocumentType.UNKNOWN,
            confidence=ConfidenceLevel.NONE,
            diagnostics=[diag],
        )

    def _disambiguate_by_proximity(self, ambiguous_res, anchor_resolution=None, clash_point=None):
        """Disambiguates an AMBIGUOUS participant by picking the candidate closest to the anchor or clash point."""
        candidate_objs = getattr(ambiguous_res, "candidate_objects", None)
        if not candidate_objs:
            return ambiguous_res

        from .coordinate_transformer import Vector3, extract_element_host_bbox

        anchor_bbox = None
        if anchor_resolution and anchor_resolution.is_resolved and anchor_resolution.element is not None:
            anchor_bbox, _ = extract_element_host_bbox(
                anchor_resolution.element, link_instance=anchor_resolution.link_instance
            )

        best_cand = None
        min_dist = float("inf")

        for cand in candidate_objs:
            cand_elem = cand.get("element")
            cand_link = cand.get("link_instance")
            c_bbox, _ = extract_element_host_bbox(cand_elem, link_instance=cand_link)
            if c_bbox is not None and anchor_bbox is not None:
                dist = anchor_bbox.min_distance_to(c_bbox)
                if dist < min_dist:
                    min_dist = dist
                    best_cand = cand
            elif c_bbox is not None and clash_point is not None:
                try:
                    cp_vec = (
                        Vector3(float(clash_point.x), float(clash_point.y), float(clash_point.z))
                        if hasattr(clash_point, "x")
                        else clash_point
                    )
                    cp_dist = (c_bbox.center - cp_vec).length()
                    if cp_dist < min_dist:
                        min_dist = cp_dist
                        best_cand = cand
                except Exception:
                    pass

        # Pick best spatial candidate, or default to first candidate
        chosen = best_cand if best_cand is not None else candidate_objs[0]
        corrob = chosen.get("corrob", {})
        dist_note = (
            " (proximity dist={0:.2f} ft)".format(min_dist)
            if min_dist != float("inf")
            else ""
        )

        if chosen["target_type"] == TargetDocumentType.HOST:
            return ParticipantResolution(
                participant=ambiguous_res.participant,
                status=ResolutionStatus.RESOLVED,
                target_type=TargetDocumentType.HOST,
                confidence=ConfidenceLevel.HIGH if best_cand else ConfidenceLevel.MEDIUM,
                host_element=chosen["element"],
                match_strategy="PROXIMITY_DISAMBIGUATION_HOST",
                corroboration=corrob,
                diagnostics=[
                    "Disambiguated to host element via proximity to clashing pair{0}.".format(dist_note)
                ] + corrob.get("warnings", []),
            )
        else:
            return ParticipantResolution(
                participant=ambiguous_res.participant,
                status=ResolutionStatus.RESOLVED,
                target_type=TargetDocumentType.LINK,
                confidence=ConfidenceLevel.HIGH if best_cand else ConfidenceLevel.MEDIUM,
                linked_element=chosen["element"],
                link_instance=chosen["link_instance"],
                linked_document=chosen["linked_document"],
                match_strategy="PROXIMITY_DISAMBIGUATION_LINK",
                corroboration=corrob,
                diagnostics=[
                    "Disambiguated to link '{0}' via proximity to clashing pair{1}.".format(
                        chosen.get("instance_name", ""), dist_note
                    )
                ] + corrob.get("warnings", []),
            )

    def resolve_clash(self, clash_result):
        """Resolves both participant A and participant B of a ClashResultItem.

        If one participant is resolved and the other is ambiguous, uses 3D spatial
        proximity to automatically select the matching link instance.

        Returns tuple: (resolution_a, resolution_b)
        """
        res_a = self.resolve(clash_result.participant_a)
        res_b = self.resolve(clash_result.participant_b)

        # Proximity disambiguation when one participant is resolved and the other is ambiguous
        if res_a.is_resolved and res_b.status == ResolutionStatus.AMBIGUOUS:
            res_b = self._disambiguate_by_proximity(
                res_b, anchor_resolution=res_a, clash_point=clash_result.clash_point
            )
        elif res_b.is_resolved and res_a.status == ResolutionStatus.AMBIGUOUS:
            res_a = self._disambiguate_by_proximity(
                res_a, anchor_resolution=res_b, clash_point=clash_result.clash_point
            )

        return (res_a, res_b)

    def resolve_all(self, sync_payload):
        """Resolves all clash participants across all tests in a ClashSyncPayload.

        Returns list of tuples: [((test_idx, clash_idx), resolution_a, resolution_b), ...]
        """
        results = []
        for t_idx, test in enumerate(sync_payload.tests):
            for c_idx, clash in enumerate(test.results):
                res_a, res_b = self.resolve_clash(clash)
                results.append(((t_idx, c_idx), res_a, res_b))
        return results


def resolve_participant(participant, host_doc, link_instances=None):
    """Convenience function to resolve a single ClashParticipant against a Revit document.

    Args:
      participant: ClashParticipant instance.
      host_doc: Revit Document or mock test document.
      link_instances: optional list of link instances.

    Returns:
      ParticipantResolution
    """
    resolver = ElementResolver(host_doc, link_instances=link_instances)
    return resolver.resolve(participant)
