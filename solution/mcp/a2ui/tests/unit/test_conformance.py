"""The conformance rules: the compiler's output passes, and each rule catches what it was written for."""

import pytest

from mcp_a2ui import conformance
from mcp_a2ui.compiler import compile

pytestmark = pytest.mark.unit

SPEC = {
    "form_id": "t_form", "title": "T",
    "fields": [{"key": "first_name"}, {"key": "has_claims"}, {"key": "/drivers", "widget": "repeater", "repeat": {"fields": [{"key": "driver_name"}]}}],
}  # fmt: skip


def surface(messages):
    """The ``createSurface`` body of the first message."""
    return messages[0]["createSurface"]


def comp(messages, component_id):
    """A component by id, or the first of a kind (``Repeater``)."""
    return next(c for c in surface(messages)["components"] if component_id in (c["id"], c["component"]))


def test_a_compiled_form_is_conformant():
    assert conformance.check(compile(SPEC, year=2026), opens_surface=True) == []


BREAKS = [  # (what is broken, how, the problem reported)
    ("version", lambda m: m[0].update(version="0.9"), "version 0.9"),
    ("two surfaces", lambda m: m.append(m[0]), "form must open with one createSurface"),
    ("no body", lambda m: m[0].pop("createSurface"), "message needs exactly one of"),
    ("two bodies", lambda m: m[0].update(deleteSurface={"surfaceId": "t_form"}), "message needs exactly one of"),
    ("no data model", lambda m: surface(m).pop("dataModel"), "createSurface needs components and dataModel"),
    ("theme", lambda m: surface(m).update(theme={}), "theme is invalid in 1.0"),
    ("surface id", lambda m: surface(m).update(surfaceId="my-form"), "surfaceId my-form"),
    ("no root", lambda m: surface(m)["components"].pop(0), "no root"),
    ("component id", lambda m: comp(m, "title").update(id="ti-tle"), "component id ti-tle"),
    ("heading variant", lambda m: comp(m, "title").update(variant="h1"), "title: heading variant (use Markdown)"),
    ("missing child", lambda m: comp(m, "root")["children"].append("ghost"), "root: missing child ghost"),
    ("missing template item", lambda m: comp(m, "Repeater")["template"].append("ghost"), "repeater_4: missing child ghost"),
    ("missing button text", lambda m: comp(m, "action_submit").update(child="ghost"), "action_submit: missing child ghost"),
    ("path binding", lambda m: comp(m, "text_1").update(value={"path": "/quote/first_name"}), "text_1: {path} binding (use @path)"),
    ("call", lambda m: comp(m, "text_1").update(value={"call": "x"}), "text_1: call (use @call)"),
    ("checks", lambda m: comp(m, "text_1").update(checks=[]), "text_1: checks (use @checks)"),
    ("directive", lambda m: comp(m, "text_1").update({"@bogus": 1}), "text_1: unknown directive @bogus"),
    ("check rule", lambda m: comp(m, "text_1")["@checks"][0].update(condition={}), "text_1: CheckRule without condition.@call"),
    ("button", lambda m: comp(m, "action_submit").update(action={}), "action_submit: Button without action.event.name"),
]  # fmt: skip


@pytest.mark.parametrize(("how", "break_it", "problem"), BREAKS, ids=[b[0] for b in BREAKS])
def test_each_rule_catches_its_break(how, break_it, problem):
    messages = compile(SPEC, year=2026)
    break_it(messages)
    assert any(problem in p for p in conformance.check(messages, opens_surface=True))


@pytest.mark.parametrize(
    ("message", "problem"),
    [
        (
            {"version": "1.0", "updateDataModel": {"surfaceId": "s_1", "path": "/a", "value": None}},
            None,
        ),  # null deletes the key
        (
            {"version": "1.0", "updateDataModel": {"surfaceId": "s_1", "path": "/a"}},
            "updateDataModel needs value (null deletes the key)",
        ),
        ({"version": "1.0", "deleteSurface": {"surfaceId": "s_1"}}, None),
        ({"version": "1.0", "deleteSurface": {"surfaceId": "s 1"}}, "deleteSurface: surfaceId s 1"),
        (
            {
                "version": "1.0",
                "updateComponents": {"surfaceId": "s_1", "components": [{"id": "text_1", "component": "TextField"}]},
            },
            None,
        ),
        (
            {
                "version": "1.0",
                "updateComponents": {"surfaceId": "s_1", "components": [{"id": "text-1", "component": "TextField"}]},
            },
            "component id text-1",
        ),
    ],
)
def test_update_and_delete_messages(message, problem):
    assert conformance.check([message]) == ([problem] if problem else [])
