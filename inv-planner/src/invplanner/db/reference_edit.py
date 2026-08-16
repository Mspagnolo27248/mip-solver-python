"""Reading and editing the reference data a projection rests on.

Yields, rates, capacities and control limits arrive from the workbook and were
previously import-only, which meant a planner could see a wrong yield but not fix
it. These are the numbers every downstream figure depends on, so they are
editable, audited, and always shown next to the value that was imported.

The pattern matches the feed staging layer deliberately: the imported value is
kept as `source_value` and the edit as `override_value`, so re-importing the
workbook refreshes the source without discarding anyone's correction.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .. import model_config as cfg
from .models import AuditEvent, ReferenceOverride, ReferenceVersion, RefKind


# ------------------------------------------------------------------ extraction
def _crude_yields(payload: Dict[str, Any]) -> List[Tuple[str, str, float, str]]:
    out = []
    products = payload.get("products", {})
    for code, months in sorted(payload.get("crude_yields", {}).items()):
        name = (products.get(code, {}) or {}).get("name", "")
        for month, value in months.items():
            out.append((code, month, float(value), name))
    return out


def _unit_yields(payload: Dict[str, Any]) -> List[Tuple[str, str, float, str]]:
    """Fixed conversion factors on the process units.

    Keyed `unit|charge|product` so a single charge feeding several products stays
    unambiguous - the MEK turns 9117 into both dewaxed oil and slack wax.
    """
    out = []
    products = payload.get("products", {})
    for rule in payload.get("yield_rules", []):
        if rule.get("kind") != "unit":
            continue
        key = "{}|{}|{}".format(rule["unit"], rule["charge_code"], rule["out"])
        charge_name = (products.get(rule["charge_code"], {}) or {}).get("name", "")
        out_name = (products.get(rule["out"], {}) or {}).get("name", "")
        label = "{} → {}".format(charge_name or rule["charge_code"],
                                 out_name or rule["out"])
        out.append((key, None, float(rule["yield"]), label))
    return sorted(out)


def _max_rates(payload: Dict[str, Any]) -> List[Tuple[str, str, float, str]]:
    out = []
    products = payload.get("products", {})
    for unit in cfg.DECISION_UNITS:
        for code, info in cfg.MAX_RATE_BBL_PER_DAY.get(unit, {}).items():
            name = (products.get(code, {}) or {}).get("name", "")
            note = "" if info["basis"] == "clean day" else " (grossed up)"
            out.append((unit, code, float(info["bbl"]), name + note))
    return out


def _tank_capacity(payload: Dict[str, Any]) -> List[Tuple[str, str, float, str]]:
    products = payload.get("products", {})
    return [(code, None, float(value),
             (products.get(code, {}) or {}).get("name", ""))
            for code, value in sorted(payload.get("tank_capacity", {}).items())]


def _targets(payload: Dict[str, Any], which: str
             ) -> List[Tuple[str, str, Optional[float], str]]:
    out = []
    for code, t in sorted(payload.get("inventory_targets", {}).items()):
        value = t.get(which)
        out.append((code, None, None if value is None else float(value),
                    t.get("name", "")))
    return out


EXTRACTORS = {
    RefKind.CRUDE_YIELD: _crude_yields,
    RefKind.UNIT_YIELD: _unit_yields,
    RefKind.MAX_RATE: _max_rates,
    RefKind.TANK_CAPACITY: _tank_capacity,
    RefKind.TARGET_LCL: lambda p: _targets(p, "lcl"),
    RefKind.TARGET_UCL: lambda p: _targets(p, "ucl"),
}


def imported_values(payload: Dict[str, Any], kind: str
                    ) -> List[Tuple[str, str, Optional[float], str]]:
    fn = EXTRACTORS.get(kind)
    return fn(payload) if fn else []


# --------------------------------------------------------------------- reading
def list_values(db: Session, rv: ReferenceVersion, kind: str, search: str = "",
                only_edited: bool = False) -> Dict[str, Any]:
    overrides = {(o.k1, o.k2): o for o in db.query(ReferenceOverride).filter(
        ReferenceOverride.reference_version_id == rv.id,
        ReferenceOverride.kind == kind)}

    rows = []
    needle = search.strip().lower()
    for k1, k2, source, label in imported_values(rv.payload, kind):
        o = overrides.get((k1, k2))
        if needle and needle not in "{} {} {}".format(
                k1, k2 or "", label or "").lower():
            continue
        edited = o is not None and o.override_value is not None
        if only_edited and not edited:
            continue
        rows.append({
            "k1": k1, "k2": k2, "label": label,
            "source_value": source,
            "override_value": o.override_value if o else None,
            "effective_value": (o.override_value if edited else source),
            "edited": edited,
            "override_by": o.override_by if o else None,
            "reason": o.reason if o else None,
            "override_at": (o.override_at.isoformat()
                            if o and o.override_at else None),
        })

    meta = RefKind.META.get(kind, {})
    return {"kind": kind, "title": meta.get("title", kind),
            "description": meta.get("description", ""),
            "unit": meta.get("unit", ""), "keys": meta.get("keys", ["", ""]),
            "total": len(rows), "edited": len([r for r in rows if r["edited"]]),
            "rows": rows}


def summary(db: Session, rv: ReferenceVersion) -> List[Dict[str, Any]]:
    counts = {}
    for o in db.query(ReferenceOverride).filter(
            ReferenceOverride.reference_version_id == rv.id,
            ReferenceOverride.override_value.isnot(None)):
        counts[o.kind] = counts.get(o.kind, 0) + 1
    out = []
    for kind in RefKind.ALL:
        meta = RefKind.META.get(kind, {})
        values = imported_values(rv.payload, kind)
        out.append({
            "kind": kind, "title": meta.get("title", kind),
            "description": meta.get("description", ""),
            "unit": meta.get("unit", ""),
            "count": len(values),
            "missing": len([v for v in values if v[2] is None]),
            "edited": counts.get(kind, 0),
        })
    return out


# --------------------------------------------------------------------- editing
def set_override(db: Session, rv: ReferenceVersion, kind: str, k1: str,
                 k2: Optional[str], value: Optional[float], actor: str,
                 reason: str = "") -> Dict[str, Any]:
    source = None
    label = ""
    for a, b, src, lbl in imported_values(rv.payload, kind):
        if a == k1 and (b or None) == (k2 or None):
            source, label = src, lbl
            break

    row = (db.query(ReferenceOverride)
           .filter(ReferenceOverride.reference_version_id == rv.id,
                   ReferenceOverride.kind == kind,
                   ReferenceOverride.k1 == k1,
                   ReferenceOverride.k2 == k2).first())
    if row is None:
        row = ReferenceOverride(reference_version_id=rv.id, kind=kind, k1=k1,
                                k2=k2, source_value=source, label=label)
        db.add(row)
    row.source_value = source
    row.label = label

    if value is None:
        row.override_value = None
        row.override_by = None
        row.override_at = None
        row.reason = None
        action = "reference.override.clear"
    else:
        row.override_value = float(value)
        row.override_by = actor
        row.override_at = dt.datetime.utcnow()
        row.reason = reason
        action = "reference.override.set"

    rv.overrides_version = (rv.overrides_version or 0) + 1
    db.add(AuditEvent(actor=actor, action=action,
                      target="{}:{}:{}".format(kind, k1, k2 or ""),
                      detail={"value": value, "source": source,
                              "reason": reason}))
    db.commit()
    return {"kind": kind, "k1": k1, "k2": k2,
            "effective_value": row.effective_value, "edited": value is not None}


# ------------------------------------------------------- applying to the engine
def apply_overrides(db: Session, rv: ReferenceVersion) -> Dict[str, Any]:
    """The reference payload with every planner edit folded in.

    Returns a copy: the imported document is never mutated, so re-importing and
    comparing against the original both stay possible.
    """
    import copy

    payload = copy.deepcopy(rv.payload)
    rows = db.query(ReferenceOverride).filter(
        ReferenceOverride.reference_version_id == rv.id,
        ReferenceOverride.override_value.isnot(None)).all()
    if not rows:
        return payload

    for o in rows:
        v = o.override_value
        if o.kind == RefKind.CRUDE_YIELD:
            payload.setdefault("crude_yields", {}).setdefault(o.k1, {})[o.k2] = v
        elif o.kind == RefKind.UNIT_YIELD:
            unit, charge, out = o.k1.split("|")
            for rule in payload.get("yield_rules", []):
                if (rule.get("kind") == "unit" and rule.get("unit") == unit
                        and rule.get("charge_code") == charge
                        and rule.get("out") == out):
                    rule["yield"] = v
        elif o.kind == RefKind.TANK_CAPACITY:
            payload.setdefault("tank_capacity", {})[o.k1] = v
        elif o.kind == RefKind.TARGET_LCL:
            payload.setdefault("inventory_targets", {}).setdefault(
                o.k1, {})["lcl"] = v
        elif o.kind == RefKind.TARGET_UCL:
            payload.setdefault("inventory_targets", {}).setdefault(
                o.k1, {})["ucl"] = v
        elif o.kind == RefKind.MAX_RATE:
            payload.setdefault("max_rate_overrides", {}).setdefault(
                o.k1, {})[o.k2] = v
    return payload
