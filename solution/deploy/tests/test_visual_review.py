"""The visual review: which screenshots are picked, the request, the markdown comment and the dry run."""

import json

import httpx

import visual_review as vr
from tests.conftest import Fake

# a tiny fake PNG: only its name and size matter
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
# a structured answer with two findings
FINDINGS = {
    "summary": "Two points on the phone layout.",
    "findings": [
        {
            "area": "contrast",
            "severity": "info",
            "screenshot": "a.png",
            "finding": "Hint text is light.",
            "suggestion": "Use --ink-2.",
        },
        {
            "area": "phone_layout",
            "severity": "fail",
            "screenshot": "b.png",
            "finding": "The composer is hidden.",
            "suggestion": "Keep it sticky.",
        },
    ],
}


def responses_payload(review: dict) -> dict:
    """A Responses API body whose message carries `review` as JSON text."""
    return {
        "output": [{"type": "reasoning"}, {"type": "message", "content": [{"type": "output_text", "text": json.dumps(review)}]}]
    }


def setup(tmp_path, count=3):
    """Create `count` screenshots in a nested folder and a style guide; return (screenshots folder, guide)."""
    shots = tmp_path / "shots" / "nested"
    shots.mkdir(parents=True)
    for i in range(count):
        (shots / f"{chr(97 + i)}.png").write_bytes(PNG)
    guide = tmp_path / "style-guide.md"
    guide.write_text("# Guide\n--g: #00874E\n", encoding="utf-8")
    return tmp_path / "shots", guide


def args(tmp_path, shots, guide, *extra):
    """Command-line arguments for a review of `shots` against `guide` that writes `out.md` in `tmp_path`."""
    return [
        "--images",
        str(shots),
        "--style-guide",
        str(guide),
        "--endpoint",
        "https://rev.openai.azure.com",
        "--model",
        "vision-1",
        "--out",
        str(tmp_path / "out.md"),
        *extra,
    ]


def test_images_are_found_recursively_sorted_and_limited(tmp_path):
    shots, _ = setup(tmp_path, count=4)
    assert [p.name for p in vr.collect_images(shots, 3)] == ["a.png", "b.png", "c.png"]


def test_failed_visual_tests_review_the_actual_images_and_skip_diffs(tmp_path):
    shots = tmp_path / "s"
    shots.mkdir()
    for name in ("a-expected.png", "a-actual.png", "a-diff.png", "b.png"):
        (shots / name).write_bytes(PNG)
    assert [p.name for p in vr.collect_images(shots, 5)] == ["a-actual.png"]
    (shots / "a-actual.png").unlink()
    assert [p.name for p in vr.collect_images(shots, 5)] == ["b.png"]


def test_request_inlines_images_and_asks_for_strict_json(tmp_path):
    shots, guide = setup(tmp_path, count=2)
    request = vr.build_request("vision-1", guide.read_text(), vr.collect_images(shots, 5))
    content = request["input"][0]["content"]
    assert "--g: #00874E" in content[0]["text"] and "brand tokens" in content[0]["text"].lower()
    assert [c["image_url"][:22] for c in content if c["type"] == "input_image"] == ["data:image/png;base64,"] * 2
    assert request["text"]["format"] == {"type": "json_schema", "name": "visual_review", "schema": vr.SCHEMA, "strict": True}
    assert request["store"] is False and request["model"] == "vision-1"


def test_prompt_covers_the_five_checks_of_the_brief():
    for topic in ("Brand tokens", "Grid alignment", "French copy", "Accessibility contrast", "Phone layout"):
        assert topic in vr.PROMPT


def test_dry_run_request_does_not_embed_images(tmp_path):
    shots, guide = setup(tmp_path, count=1)
    request = vr.build_request("m", "g", vr.collect_images(shots, 5), embed=False)
    assert "base64" not in json.dumps(request) and "omitted in dry run" in json.dumps(request)


def test_main_writes_a_markdown_comment_sorted_by_severity(tmp_path):
    shots, guide = setup(tmp_path)
    fake = Fake({("POST", "/openai/v1/responses"): responses_payload(FINDINGS)})
    from common import Client

    client = Client("https://rev.openai.azure.com", None, transport=httpx.MockTransport(fake))
    assert vr.main(args(tmp_path, shots, guide), client=client) == 0
    text = (tmp_path / "out.md").read_text(encoding="utf-8")
    assert text.startswith("<!-- visual-review -->") and "never blocks a merge" in text and "3 screenshots" in text
    assert text.index("FAIL") < text.index("INFO")
    assert json.loads(fake.requests[0].content)["model"] == "vision-1"


def test_main_without_screenshots_says_so_and_calls_nothing(tmp_path):
    guide = tmp_path / "g.md"
    guide.write_text("x")
    (tmp_path / "empty").mkdir()
    assert vr.main(args(tmp_path, tmp_path / "empty", guide)) == 0
    assert "No screenshots" in (tmp_path / "out.md").read_text()


def test_main_dry_run_prints_the_request_and_writes_nothing(tmp_path, capsys):
    shots, guide = setup(tmp_path)
    assert vr.main(args(tmp_path, shots, guide, "--dry-run")) == 0
    line = json.loads(capsys.readouterr().out)
    assert line["url"] == "https://rev.openai.azure.com/openai/v1/responses" and not (tmp_path / "out.md").exists()


def test_no_findings_renders_a_clean_review():
    text = vr.to_markdown({"summary": "All good.", "findings": []}, 4)
    assert "No findings." in text and "| Area |" not in text


def test_print_prompt_prints_the_prompt_and_nothing_else(capsys):
    assert vr.main(["--print-prompt"]) == 0
    assert capsys.readouterr().out.strip() == vr.PROMPT.strip()
