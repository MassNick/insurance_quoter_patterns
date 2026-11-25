"""The Python compiler against the JavaScript reference compiler (demo/src/a2ui.js) on every demo FormSpec.

golden.json holds the 33 FormSpecs of demo/src/forms.js and the 7 of extra.json (widgets and options the demo does not
use), and what the JavaScript compiler makes of each, in French and English, with the clock frozen. With Node installed the
test regenerates it (gen_golden.mjs) and also requires the committed file to be current; without Node it uses the committed
file. Regenerate with ``node tests/golden/gen_golden.mjs``.
"""

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from quote_common.contracts import contracts_dir

from mcp_a2ui import conformance
from mcp_a2ui.compiler import compile, validate

pytestmark = pytest.mark.golden
HERE = Path(__file__).parent
STORED = json.loads((HERE / "golden.json").read_text(encoding="utf-8"))
FORM_IDS = [f["id"] for f in STORED["forms"]]
# The one intended difference (README, "Deviations"): the JavaScript compiler leaves a repeater's default labels in English
# in the French UI; the Python compiler says "Élément {n}" and "Ajouter".
FRENCH_REPEATER_DEFAULTS = {"Item {n}": "Élément {n}", "Add another": "Ajouter"}


def canonical(value):
    """``value`` as sorted, compact JSON: equal values give the same bytes."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory):
    """golden.json as the JavaScript compiler makes it now, or None when Node is not installed."""
    node = shutil.which("node")
    if node is None:
        return None
    out = tmp_path_factory.mktemp("golden") / "golden.json"
    command = [
        node,
        str(HERE / "gen_golden.mjs"),
        "--catalog",
        str(contracts_dir() / "state-catalog.json"),
        "--out",
        str(out),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_committed_golden_file_is_current(regenerated):
    if regenerated is None:
        pytest.skip("node is not installed: the committed golden.json is used as it is")
    assert regenerated == STORED, "golden.json is stale: run `node tests/golden/gen_golden.mjs` and commit the result"


def test_every_demo_form_and_the_extras_are_covered():
    assert len([i for i in FORM_IDS if not i.startswith("extra-")]) >= 33 and len(set(FORM_IDS)) == len(FORM_IDS)
    assert any(i.startswith("extra-") for i in FORM_IDS)


@pytest.mark.parametrize("lang", ["fr", "en"])
@pytest.mark.parametrize("form_id", FORM_IDS)
def test_python_output_equals_the_javascript_output(regenerated, form_id, lang):
    golden = regenerated or STORED
    form = next(f for f in golden["forms"] if f["id"] == form_id)
    expected = copy.deepcopy(form[lang])
    if lang == "fr":
        for c in expected[0]["createSurface"]["components"]:
            if c["component"] == "Repeater":
                c["itemLabel"] = FRENCH_REPEATER_DEFAULTS.get(c["itemLabel"], c["itemLabel"])
                c["addLabel"] = FRENCH_REPEATER_DEFAULTS.get(c["addLabel"], c["addLabel"])
    actual = compile(form["spec"], lang=lang, year=golden["year"])
    assert actual == expected
    assert canonical(actual) == canonical(expected)  # the same bytes once canonical, not only equal values


@pytest.mark.parametrize("form_id", FORM_IDS)
def test_demo_form_validates_and_both_outputs_are_conformant(form_id):
    form = next(f for f in STORED["forms"] if f["id"] == form_id)
    assert validate(form["spec"]) == []
    assert (
        conformance.check(form["fr"], opens_surface=True) == conformance.check(form["en"], opens_surface=True) == []
    )  # the JavaScript output
    assert conformance.check(compile(form["spec"], year=STORED["year"]), opens_surface=True) == []
