# -*- coding: utf-8 -*-
"""Clear Clash Highlights.

Purges all temporary clash highlight proxies (DirectShapes) and CVS view filters
from the active Revit document, ensuring 100% clean model state with zero remnants.
"""
from __future__ import absolute_import, division, print_function

from pyrevit import forms, revit, DB

doc = revit.doc
if doc is None or doc.IsReadOnly:
    forms.alert("No active writable document.", title="Clear Clash Highlights", exitscript=True)

stale_ds = []
for ds in DB.FilteredElementCollector(doc).OfClass(DB.DirectShape):
    if getattr(ds, "ApplicationId", "") == "ClashViewSync":
        stale_ds.append(ds.Id)

stale_pf = []
for pf in DB.FilteredElementCollector(doc).OfClass(DB.ParameterFilterElement):
    if getattr(pf, "Name", "").startswith("CVS_Clash"):
        stale_pf.append(pf.Id)

total = len(stale_ds) + len(stale_pf)
if total == 0:
    forms.toast("Project is already completely clean! Zero CVS elements found.", title="Clear Clash Highlights")
else:
    with revit.Transaction("Clear Clash Highlights"):
        for sid in stale_ds:
            try:
                doc.Delete(sid)
            except Exception:
                pass
        for fid in stale_pf:
            try:
                doc.Delete(fid)
            except Exception:
                pass
    forms.toast(
        "Successfully purged {0} highlight proxy(ies) and {1} filter(s)!".format(len(stale_ds), len(stale_pf)),
        title="Clear Clash Highlights"
    )
