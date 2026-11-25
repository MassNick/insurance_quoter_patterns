"""FormSpec -> A2UI 1.0 compiler.

A Python port of ``compile()`` in the design demo (``demo/src/a2ui.js``; the golden test runs both and requires the same
output for every demo FormSpec), plus what the demo does not do: prefill from the committed session state, an edit mode,
and validation of the FormSpec.

One ``createSurface`` message opens a form. It carries ``components`` (a flat list with identifier ids, ``{"@path"}``
bindings and ``"@checks"`` CheckRules) and ``dataModel``. Where a prefilled value came from is kept next to it, at
``/_provenance/<escaped pointer>`` = ``{source, note}``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator
from datetime import date
from typing import Any

from jsonschema import Draft202012Validator
from quote_common import pointer
from quote_common.catalog import ENTITY_BASE, get_catalog
from quote_common.contracts import load_schema

A2UI_VERSION = "1.0"  # the "version" of every message
# The component catalog every surface declares.
CATALOG_ID = "https://catalog.example.com/insurance-catalog/v1/catalog.json"
BADGE_SOURCES = ("chat", "document", "lookup")  # a value typed in a form needs no "from ..." badge

# The catalog keys behind the properties of YmmPicker (under the vehicle) and AddressLookup (under /quote).
_YMM = {"year": "vehicle_year", "make": "vehicle_make", "model": "vehicle_model", "vehicleType": "vehicle_type"}
_ADDRESS = {"line1": "address_line1", "city": "city", "province": "province_code", "postal": "postal_code"}
# Widgets narrow enough to share a row in the two-column layout; the others take a full row.
_NARROW = {"text", "number", "date", "select", "toggle", "currency", "email", "phone", "postal_code", "vin"}
# Catalog field type -> widget, when the FormSpec says "auto" (enums and booleans are decided in _auto_widget).
_AUTO_WIDGET = {
    "multi_enum": "chips",
    "date": "date",
    "integer": "number",
    "currency": "currency",
    "email": "email",
    "phone": "phone",
    "postal_code": "postal_code",
    "vin": "vin",
    "assignment_matrix": "assignment_board",
}
_PATTERN_MESSAGES = {  # (fr, en); any other pattern gets "Format invalide"
    "phone_number": ("Entrez un numéro à 10 chiffres", "Enter a 10-digit phone number"),
    "postal_code": ("Entrez un code postal canadien, comme H2X 1Y4", "Enter a Canadian postal code, like H2X 1Y4"),
    "driver_license_number": ("Format du permis du Québec : A1234-123456-12", "Quebec licence format: A1234-123456-12"),
}
# The supported visible_if form: <key or /pointer> == true | false | 'text' | "text" | number.
_VISIBLE_IF = re.compile(r"""^\s*([a-z0-9_/]+)\s*==\s*(true|false|'[^']*'|"[^"]*"|\d+)\s*$""", re.IGNORECASE)
# A pointer into one of the five parts of the session document.
_ENTITY_POINTER = re.compile(r"^/(quote|vehicles|drivers|underwriting|household)(/|$)")


def parse_visible_if(expr: str, base_path: str) -> dict | None:
    """``outside_quebec == true`` -> an ``equals`` call on the sibling path; ``None`` when the expression is not supported."""
    m = _VISIBLE_IF.match(expr or "")
    if not m:
        return None
    raw = m[2]
    value = {"true": True, "false": False}.get(raw.lower(), int(raw) if raw.isdigit() else raw[1:-1])
    path = m[1] if m[1].startswith("/") else f"{base_path}/{m[1]}"
    return {"@call": "equals", "args": {"left": {"@path": path}, "right": value}}


def message(kind: str, **body: Any) -> dict:
    """One A2UI 1.0 message: ``message("deleteSurface", surfaceId="s")`` -> ``{"version": "1.0", "deleteSurface": {...}}``."""
    return {"version": A2UI_VERSION, kind: body}


def _auto_widget(d: dict | None) -> str:
    """The widget for a catalog field when the FormSpec says ``auto``."""
    if not d:
        return "text"
    if d["type"] == "enum":
        return "select" if len(d["enum"]) > 7 else "chips"
    if d["type"] == "boolean":
        return "checkbox" if d["key"] == "contact_consent" else "toggle"
    return _AUTO_WIDGET.get(d["type"], "text")


def _rule(call: str, path: str | None, message: str, **args: Any) -> dict:
    """An A2UI 1.0 CheckRule. Arguments that are ``None`` are left out."""
    args = {k: v for k, v in args.items() if v is not None}
    return {"condition": {"@call": call, "args": {"value": {"@path": path}, **args}}, "message": message}


def _bind(base_path: str, props: dict[str, str]) -> dict[str, dict]:
    """``{"year": "vehicle_year"}`` -> ``{"year": {"@path": "<base_path>/vehicle_year"}}`` for components bound to several paths."""
    return {prop: {"@path": f"{base_path}/{key}"} for prop, key in props.items()}


def _walk_state(node: Any, path: str, env: dict | None = None) -> Iterator[tuple[str, Any, dict | None]]:
    """Every ``(pointer, value, envelope)`` in the session state. A field is wrapped in an envelope
    (``{value, source, status, ...}``); the items of a list field are plain values under its envelope."""
    if isinstance(node, dict) and "value" in node and "source" in node:
        env, node = node, node["value"]
    yield path, node, env
    children = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    for key, child in children:
        yield from _walk_state(child, f"{path}/{pointer.escape(key)}", env)


class _Compiler:
    """Compiles one FormSpec into the components and the data model of one surface; ``run`` does the work."""

    def __init__(self, spec: dict, state: dict | None, edit: bool, lang: str, year: int) -> None:
        """``edit`` is the edit mode and ``year`` the clock for date limits. The committed ``state`` is read once."""
        self.spec, self.edit, self.year = spec, edit, year
        self.fr = lang[:2] == "fr"
        self.lang = "fr" if self.fr else "en"
        self.cat = get_catalog()
        self.comps: list[dict] = []  # the flat component list of the surface
        self.data: dict = {}  # the data model of the surface
        self.n = 0  # counter behind nid()
        self.known: dict[str, tuple[Any, dict | None]] = {}  # bound path -> (value, envelope), from the committed state
        for base in ENTITY_BASE.values():
            node = (state or {}).get(base[1:])
            self.known.update({p: (v, e) for p, v, e in _walk_state(node, base) if v is not None})

    def L(self, fr: str, en: str) -> str:
        """The French or the English text, by the language of the surface."""
        return fr if self.fr else en

    def nid(self, prefix: str) -> str:
        """A fresh component id: ``prefix`` and a running number."""
        self.n += 1
        return f"{prefix}_{self.n}"

    def add(self, comp: dict) -> str:
        """Add a component to the surface; returns its id."""
        self.comps.append(comp)
        return comp["id"]

    def prefill(self, bound: list[str], explicit: dict[str, Any], path: str | None, pf: dict) -> None:
        """Fill the data model: the FormSpec's explicit prefill first, then every other bound path from the state."""

        def badge(p: str, source: str, note: str | None) -> None:
            """Record where the value at ``p`` came from, with the ``note`` (the evidence) when there is one."""
            pointer.set(self.data, pointer.provenance_path(p), {"source": source, **({"note": note} if note else {})})

        for p, value in explicit.items():
            pointer.set(self.data, p, copy.deepcopy(value))
        if path and (pf.get("source") or pf.get("note")):
            badge(path, pf.get("source") or "chat", pf.get("note"))
        for p in bound:
            if p not in explicit and p in self.known:
                value, env = self.known[p]
                pointer.set(self.data, p, copy.deepcopy(value))
                if env and env["source"] in BADGE_SOURCES:
                    badge(p, env["source"], env.get("evidence"))

    def checks(self, d: dict | None, required: bool, path: str | None) -> list[dict]:
        """CheckRules from the catalog: required, email, regex, VIN check digit, ranges, age and date window."""
        L, out = self.L, []
        if required:
            out.append(_rule("required", path, L("Ce champ est obligatoire", "This field is required")))
        if not d:
            return out
        if d["type"] == "email":
            out.append(_rule("email", path, L("Entrez une adresse courriel valide", "Enter a valid email address")))
        if d["type"] == "vin":
            out.append(
                _rule(
                    "vinCheckDigit",
                    path,
                    L("Ce NIV n’est pas valide (chiffre de contrôle)", "This VIN is not valid (check digit)"),
                )
            )
        elif d.get("pattern"):
            message = L(*_PATTERN_MESSAGES.get(d["key"], ("Format invalide", "Invalid format")))
            out.append(_rule("regex", path, message, pattern=d["pattern"]))
        lo, hi = d.get("min"), d.get("max")
        if hi is None and d.get("max_offset_years") is not None:
            hi = self.year + d["max_offset_years"]
        if lo is not None or hi is not None:
            frm, to = (" de ", " à ") if self.fr else (" from ", " to ")
            message = (
                L("Entrez une valeur", "Enter a value")
                + (f"{frm}{lo}" if lo is not None else "")
                + (f"{to}{hi}" if hi is not None else "")
            )
            out.append(_rule("numericRange", path, message, min=lo, max=hi))
        if d.get("min_age"):
            message = L(f"Vous devez avoir {d['min_age']} ans ou plus", f"Must be {d['min_age']} or older")
            out.append(_rule("ageBetween", path, message, min=d["min_age"], max=d.get("max_age")))
        if d.get("min_offset_days") is not None:
            days = d.get("max_offset_days")
            message = L(f"Choisissez une date dans les {days} prochains jours", f"Pick a date in the next {days} days")
            out.append(_rule("dateWithin", path, message, from_days=d["min_offset_days"], to_days=days))
        return out

    def field(self, f: dict, rel: bool = False) -> tuple[str, bool]:
        """Compile one FormSpec field. Returns its component id and whether it takes a full row.
        ``rel`` is set for the fields of a repeater template: their ``@path`` is relative to the item."""
        L, cat, spec = self.L, self.cat, self.spec
        key = f.get("key")
        path = (key if rel else cat.resolve_path(key, spec.get("entity"))) if key else None
        d = cat.field(pointer.key_of(key) if key.startswith("/") else key) if key else None
        widget = f.get("widget") or "auto"
        if widget == "auto":
            widget = _auto_widget(d)
        required = (
            f["required"] if f.get("required") is not None else bool(d and d.get("required_for_quote") and not rel)
        )
        base = {"label": f.get("label") or (cat.label(d["key"], self.lang) if d else "")}
        optional = {
            "hint": f.get("hint"),
            "help": f.get("help") or (d and cat.help(d["key"], self.lang)) or None,
            "required": required or None,
            "readonly": f.get("readonly") or None,
            "deterministic": True if d and d.get("deterministic") else None,
        }
        base.update({k: v for k, v in optional.items() if v is not None})
        if f.get("visible_if") and path:
            base["visibleIf"] = parse_visible_if(f["visible_if"], pointer.parent_of(path))
        if f.get("field_actions") is not None:
            base["fieldActions"] = f["field_actions"]
        if checks := self.checks(d, required, path):
            base["@checks"] = checks
        if path and not rel:
            base["provenance"] = {"@path": pointer.provenance_path(path)}

        id_ = self.nid(widget)
        pf, data = f.get("prefill") or {}, f.get("data") or {}
        bound = [path] if path else []
        explicit = {path: pf["value"]} if path and "value" in pf else {}

        def mk(component: str, **props: Any) -> dict:
            """This field's component: its non-None ``props``, then the shared label, hint, checks and so on."""
            return {"id": id_, "component": component, **{k: v for k, v in props.items() if v is not None}, **base}

        value = {"@path": path}
        match widget:
            case "info":
                sep = "\u00a0: " if self.fr else ": "
                text = sep.join(filter(None, (f.get("label"), f.get("hint"))))
                return self.add(
                    {"id": id_, "component": "InPlaceNote", "tone": f.get("tone") or "info", "text": text}
                ), True
            case "text" | "email" | "phone" | "postal_code" | "number" | "currency":
                kind = (
                    ("licence" if d and d["key"] == "driver_license_number" else "shortText")
                    if widget == "text"
                    else widget
                )
                c = mk("TextField", textFieldType=kind, value=value)
                c.update({k: f[k] for k in ("placeholder", "computed") if f.get(k)})
            case "date":
                c = mk("DateTimeInput", enableDate=True, enableTime=False, value=value)
            case "select" | "chips" | "radio" | "segmented" | "time_slots":
                entries = (
                    f["options"] if f.get("options") is not None else (d and cat.enum_labels(d["key"], self.lang)) or []
                )
                options = [{"value": str(e[0]), "label": e[1]} for e in entries]
                c = mk(
                    "ChoicePicker", variant="slots" if widget == "time_slots" else widget, options=options, value=value
                )
                if d and d["type"] == "multi_enum":
                    c.update(multiple=True, maxAllowedSelections=len(options))
            case "toggle":
                options = [{"value": "true", "label": L("Oui", "Yes")}, {"value": "false", "label": L("Non", "No")}]
                c = mk("ChoicePicker", variant="segmented", valueType="boolean", options=options, value=value)
            case "checkbox":
                c = mk("CheckBox", value=value)
            case "slider":
                d = d or {}
                c = mk(
                    "Slider",
                    value=value,
                    minValue=d["min"] if d.get("min") is not None else 0,
                    maxValue=f.get("max") or d.get("max") or 100,
                    step=d.get("step") or 1,
                    unit=f.get("unit") or ("km" if d.get("key") == "annual_kilometers" else ""),
                )
            case "ymm":
                b = (
                    pointer.parent_of(path)
                    if path
                    else cat.entity_path("vehicle", (spec.get("entity") or {}).get("index"))
                )
                binds = _bind(b, _YMM)
                c = mk("YmmPicker", **binds)
                c["label"] = f.get("label") or L("Année, marque et modèle", "Year, make and model")
                message = L(
                    "Choisissez une année, une marque et un modèle valides", "Pick a valid year, make and model"
                )
                c["@checks"] = [
                    _rule("ymmValid", f"{b}/vehicle_year", message, make=binds["make"], model=binds["model"])
                ]
                c["required"] = f.get("required") is not False  # the full form lets an unknown vehicle stay empty
                bound = [v["@path"] for v in binds.values()]
                given = pf.get("value") if isinstance(pf.get("value"), dict) else {}
                explicit = {f"{b}/{k}": given[k] for k in _YMM.values() if given.get(k) is not None}
            case "vin":
                c = mk("VinField", value=value, decode=True)
            case "address_lookup":
                binds = _bind("/quote", _ADDRESS)
                c = mk("AddressLookup", **binds)
                c["label"] = f.get("label") or L("Adresse du domicile", "Home address")
                bound = [v["@path"] for v in binds.values()]
                explicit = (
                    {f"/quote/{k}": v for k, v in pf["value"].items()} if isinstance(pf.get("value"), dict) else {}
                )
            case "textarea":
                c = mk("TextField", textFieldType="longText", value=value)
            case "document_drop":
                c = mk("DocumentDrop", documents=data.get("documents") or [], read=data.get("read") or [])
                bound = [p for doc in c["documents"] for p in (doc.get("fills") or {}).values()]
                explicit = dict(data.get("values") or {})
            case "assignment_board":
                vp = path or "/household/driver_vehicle_assignments"
                c = mk(
                    "AssignmentBoard",
                    value={"@path": vp},
                    drivers=data.get("drivers") or [],
                    vehicles=data.get("vehicles") or [],
                )
                c["label"] = f.get("label") or L("Qui conduit quoi", "Who drives what")
                message = L(
                    "Chaque véhicule a besoin d’un conducteur principal",
                    "Each vehicle needs exactly one principal driver",
                )
                c["@checks"] = [_rule("onePrincipalPerVehicle", vp, message)]
                bound, explicit = [vp], ({vp: pf["value"]} if "value" in pf else {})
            case "file":
                c = mk("FileDrop", purpose=data.get("purpose") or "licence", fills=data.get("fills") or {})
            case "repeater":
                r = f.get("repeat") or {}
                coll = key if key and key.startswith("/") else "/drivers"
                start = r["start"] if r.get("start") is not None else 0
                template_fields = r.get("fields") or []
                template = [self.field(tf, rel=True)[0] for tf in template_fields]
                keys = [tf["key"] for tf in template_fields if tf.get("key")]
                limit, count = r.get("max") or 25, r.get("count") or 1
                rows = 0  # rows the committed state already holds
                while any(f"{coll}/{start + rows}/{k}" in self.known for k in keys):
                    rows += 1
                count = max(count, min(rows, limit))
                compact = len(template_fields) == 1 and template_fields[0].get("widget") == "vin"
                c = mk(
                    "Repeater",
                    collection=coll,
                    start=start,
                    count=count,
                    min=r["min"] if r.get("min") is not None else 0,
                    max=limit,
                    itemLabel=r.get("item_label") or L("Élément {n}", "Item {n}"),
                    template=template,
                    layout="list" if compact else "grid",
                    addLabel=r.get("add_label") or L("Ajouter", "Add another"),
                )
                bound = [f"{coll}/{start + i}/{k}" for i in range(count) for k in keys]
                explicit = {
                    f"{coll}/{start + i}/{k}": v
                    for i, item in enumerate(r.get("prefill") or [])
                    for k, v in item.items()
                }
            case "matrix":
                vp = path or "/household/driver_vehicle_assignments"
                roles = cat.field("driver_vehicle_assignments")
                roles = (roles.get("item_role_fr") if self.fr else None) or roles["item"]["role"]
                choices = data.get("choices") or [{"value": e[0], "label": e[1]} for e in roles]
                c = mk(
                    "AssignmentMatrix",
                    value={"@path": vp},
                    rows=data.get("rows") or [],
                    cols=data.get("cols") or [],
                    choices=choices,
                )
                bound, explicit = [vp], ({vp: pf["value"]} if "value" in pf else {})
            case "offer_cards":
                vp = path or "/underwriting/offer_type"
                c = mk(
                    "OfferCards",
                    value={"@path": vp},
                    offers=data.get("offers") or [],
                    recommended=data.get("recommended"),
                    showAdjust=True if data.get("show_adjust") else None,
                )
                bound, explicit = [vp], ({vp: pf["value"]} if "value" in pf else {})
            case "summary":
                c = mk("Summary", items=data.get("items") or [])
            case _:
                c = mk("TextField", textFieldType="shortText", value=value)

        if not rel:
            self.prefill(bound, explicit, path, pf)
        wide = widget not in _NARROW or (widget == "select" and f.get("widget") != "select" and len(c["options"]) > 3)
        return self.add(c), wide

    def run(self) -> list[dict]:
        """Build the surface: title, intro, the fields (in rows or full width, by the layout), the action bar and the
        root column. Returns the one ``createSurface`` message."""
        spec, L = self.spec, self.L
        sid = re.sub(r"[^A-Za-z0-9_]", "_", str(spec["form_id"]))
        root = [self.add({"id": "title", "component": "Text", "text": "## " + spec["title"]})]
        if spec.get("intro"):
            root.append(
                self.add(
                    {
                        "id": "intro",
                        "component": "InPlaceNote",
                        "tone": spec.get("intro_tone") or "chat",
                        "text": spec["intro"],
                    }
                )
            )

        row: list[str] = []

        def flush() -> None:
            """Close the current row: a single field goes straight to the root, two share a ``Row``."""
            if len(row) == 1:
                root.append(row[0])
            elif row:
                root.append(self.add({"id": self.nid("row"), "component": "Row", "children": list(row)}))
            row.clear()

        for f in spec["fields"]:
            if f.get("widget") == "section":
                flush()
                root.append(
                    self.add(
                        {"id": self.nid("section_title"), "component": "Text", "text": "### " + f.get("label", "")}
                    )
                )
                continue
            cid, wide = self.field(f)
            if (spec.get("layout") or "two_column") == "two_column" and not wide:
                row.append(cid)
                if len(row) == 2:
                    flush()
            else:
                flush()
                root.append(cid)
        flush()

        actions = spec.get("actions")
        if actions is None:
            actions = [{"id": "submit", "label": L("Continuer", "Continue"), "style": "primary"}]
        buttons = []
        for a in actions:
            label = L("Enregistrer", "Save") if self.edit and a.get("style") == "primary" else a["label"]
            text_id = self.add({"id": f"action_{a['id']}_label", "component": "Text", "text": label})
            button = {"id": f"action_{a['id']}", "component": "Button", "child": text_id}
            button["action"] = {"event": {"name": a["id"], "context": {"surfaceId": sid}}}
            if a.get("style") in ("primary", "link"):
                button["variant"] = "primary" if a["style"] == "primary" else "borderless"
            buttons.append(self.add(button))
        root.append(self.add({"id": "actions", "component": "ActionBar", "children": buttons}))
        self.comps.insert(0, {"id": "root", "component": "Column", "children": root})
        return [
            message("createSurface", surfaceId=sid, catalogId=CATALOG_ID, dataModel=self.data, components=self.comps)
        ]


def compile(
    spec: dict, state: dict | None = None, mode: str = "new", lang: str = "fr", *, year: int | None = None
) -> list[dict]:
    """Compile a FormSpec into the A2UI 1.0 messages that open its surface (always one ``createSurface``).

    ``state`` is the BFF session state (a field envelope ``{value, source, ...}`` per leaf). Every bound path the FormSpec
    does not prefill itself is filled from it, with provenance for values that came from chat, a document or a lookup.
    ``mode="edit"`` (a reopened form) relabels the primary button "Enregistrer". ``year`` pins the clock for tests.

    FR-INT-2: until ``/quote/contact_consent`` is confirmed in ``state``, personal-data fields (``pii`` in the catalog)
    are left out, except on the end-of-form surface, which carries its own consent box. Without ``state`` the spec
    compiles as given.
    """
    if state is not None and spec.get("form_id") != "end_of_form" and not _consented(state):
        spec = {**spec, "fields": _without_pii(spec.get("fields", []))}
    return _Compiler(spec, state, mode == "edit", lang, year or date.today().year).run()


def _consented(state: dict) -> bool:
    """Whether the state holds a confirmed ``contact_consent`` of true."""
    env = (state.get("quote") or {}).get("contact_consent") or {}
    return env.get("value") is True and env.get("status") == "confirmed"


def _without_pii(fields: list[dict]) -> list[dict]:
    """The fields without personal data (``pii`` in the catalog), also inside the templates of repeaters."""
    cat, kept = get_catalog(), []
    for f in fields:
        if (cat.field(f.get("key") or "") or {}).get("pii"):
            continue
        if "fields" in (f.get("repeat") or {}):
            f = {**f, "repeat": {**f["repeat"], "fields": _without_pii(f["repeat"]["fields"])}}
        kept.append(f)
    return kept


# The FormSpec contract and its validator; render_form also gives the schema to the model.
FORM_SPEC_SCHEMA = load_schema("form-spec.schema.json")
_VALIDATOR = Draft202012Validator(FORM_SPEC_SCHEMA)


def _catalog_errors(fields: list[dict], where: str, item_keys: Any = ()) -> Iterator[str]:
    """Catalog problems in a list of fields: unknown keys, bad pointers and unsupported ``visible_if``, also inside
    repeaters. ``where`` is the path shown in messages; ``item_keys`` the item keys of a list field."""
    cat = get_catalog()
    for i, f in enumerate(fields):
        w, key = f"{where}[{i}]", f.get("key") or ""
        if key.startswith("/") and not _ENTITY_POINTER.match(key):
            yield f"{w}.key: {key!r} must start with /quote, /vehicles, /drivers, /underwriting or /household"
        elif key and not key.startswith("/") and not cat.has(key) and key not in item_keys:
            yield f"{w}.key: unknown catalog key {key!r}"
        if f.get("visible_if") and parse_visible_if(f["visible_if"], "") is None:
            yield f"{w}.visible_if: {f['visible_if']!r} is not supported (use: key == true, false, a number or 'text')"
        if rep := f.get("repeat"):
            items = (cat.field(pointer.key_of(key)) or {}).get("item", {}) if key.startswith("/") else {}
            yield from _catalog_errors(rep.get("fields", []), f"{w}.repeat.fields", items)


def _where(location: Any) -> str:
    """A JSON path as the caller wrote it: ``fields[2].widget``, or ``$`` for the root."""
    return "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in location).lstrip(".") or "$"


def schema_problems(validator: Draft202012Validator, instance: Any) -> list[str]:
    """The errors of ``instance`` against a JSON schema, as ``path: message``, at most 10."""
    return [f"{_where(e.absolute_path)}: {e.message}" for e in validator.iter_errors(instance)][:10]


def validate(spec: Any) -> list[str]:
    """What is wrong with a FormSpec, one message per problem naming the field (``fields[2].widget: ...``).
    Empty when it can be compiled. Checks the JSON schema first, then the catalog keys, pointers and ``visible_if``."""
    return schema_problems(_VALIDATOR, spec) or list(_catalog_errors(spec["fields"], "fields"))[:10]
