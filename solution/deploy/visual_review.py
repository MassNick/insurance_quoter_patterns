#!/usr/bin/env python3
"""AI visual review of a web/ pull request: option B of two (option A is the Claude Code action in visual-review.yml).

Sends the Playwright screenshots and docs/style-guide.md to a vision model on an Azure OpenAI / Foundry endpoint through the
Responses API, asks for structured findings and writes a markdown PR comment. Advisory: the exit code is 0 whatever the model says.
Use a review endpoint that GitHub-hosted runners can reach (not the private quoting account) and Entra auth (azure/login with OIDC).

  POST {endpoint}/openai/v1/responses
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from common import FOUNDRY_SCOPE, Client, add_common_args, env, require

# what a finding can be about: the five checks of the prompt, or other
AREAS = ["brand_tokens", "grid_alignment", "french_copy", "contrast", "phone_layout", "other"]
# the strict structured output of the review
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "findings"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["area", "severity", "screenshot", "finding", "suggestion"],
                "properties": {
                    "area": {"type": "string", "enum": AREAS},
                    "severity": {"type": "string", "enum": ["info", "warn", "fail"]},
                    "screenshot": {"type": "string"},
                    "finding": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
            },
        },
    },
}
# the review instructions, sent with the style guide and the screenshots
PROMPT = """You review screenshots of the Desjardins Assurances quote assistant (customer UI, French first) against the style guide below.
Check, in this order, and only what you can actually see:
1. Brand tokens: colors from the token table (green #00874E family, mints, ink grays, warn and danger), radii, pill buttons, focus ring, headings light and green.
2. Grid alignment: every form field on the 12-column grid, 6+6 rows, no field outside the grid or with a fixed width, consistent gaps.
3. French copy (fr-CA): vouvoiement, plain words, typographic apostrophe, a non-breaking space before : ; ? ! % and inside « », amounts like 1 420 $, buttons that start with a verb, no English or raw field codes in customer-facing text.
4. Accessibility contrast: text and controls meet WCAG AA (4.5:1 text, 3:1 large text and controls), a visible focus indicator, touch targets of 44 px or more.
5. Phone layout (screenshots about 390 px wide): the form covers the conversation, suggestions and composer stay usable, bottom tab bar and sheets.
Report a finding only when the screenshot shows it. Use severity fail for a clear style-guide violation, warn for a doubtful one, info for a note.
Say which screenshot (file name) each finding comes from. Do not repeat the style guide. Keep each finding to two sentences. If everything conforms, return no findings."""


def collect_images(folder: Path, limit: int) -> list[Path]:
    """What the PR looks like: the *-actual.png of the failed visual tests when there are any, else every screenshot
    except the diff and expected images."""
    pngs = sorted(p for p in folder.rglob("*.png") if p.is_file())
    actual = [p for p in pngs if p.name.endswith("-actual.png")]
    return (actual or [p for p in pngs if not p.name.endswith(("-diff.png", "-expected.png"))])[:limit]


def build_request(model: str, style_guide: str, images: list[Path], embed: bool = True) -> dict:
    """The Responses API body. With embed=False the images are described, not inlined (for --dry-run)."""
    content: list[dict] = [{"type": "input_text", "text": f"{PROMPT}\n\n--- STYLE GUIDE ---\n{style_guide}"}]
    for image in images:
        content.append({"type": "input_text", "text": f"Screenshot file: {image.name}"})
        if embed:
            data = base64.b64encode(image.read_bytes()).decode()
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{data}"})
        else:
            content.append(
                {"type": "input_image", "image_url": f"(omitted in dry run) {image.name}, {image.stat().st_size} bytes"}
            )
    return {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "text": {"format": {"type": "json_schema", "name": "visual_review", "schema": SCHEMA, "strict": True}},
        "store": False,
    }


def output_text(response: dict) -> str:
    """The text of the model's answer in a Responses API body."""
    return "".join(
        part.get("text", "")
        for item in response.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )


def to_markdown(review: dict, images: int) -> str:
    """The PR comment: the summary and a findings table sorted fail, warn, info (`images` = screenshots reviewed)."""
    icon = {"fail": "FAIL", "warn": "WARN", "info": "INFO"}
    lines = ["<!-- visual-review -->", "## Visual review (advisory)", "", review["summary"], ""]
    if review["findings"]:
        lines += ["| | Area | Screenshot | Finding | Suggestion |", "|---|---|---|---|---|"]
        for f in sorted(review["findings"], key=lambda f: ["fail", "warn", "info"].index(f["severity"])):
            lines.append(f"| {icon[f['severity']]} | {f['area']} | `{f['screenshot']}` | {f['finding']} | {f['suggestion']} |")
    else:
        lines.append("No findings.")
    lines += [
        "",
        f"_{images} screenshots reviewed against `docs/style-guide.md` by an AI model. This review never blocks a merge._",
    ]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the flags; the endpoint and the model may come from the environment."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(p)
    p.add_argument("--images", type=Path, help="folder with the screenshots (searched recursively for *.png)")
    p.add_argument("--print-prompt", action="store_true", help="print the review prompt and exit (the Claude action reuses it)")
    p.add_argument("--style-guide", type=Path, default=Path("docs/style-guide.md"))
    p.add_argument(
        "--endpoint",
        default=env("VISUAL_REVIEW_ENDPOINT"),
        help="https://<resource>.openai.azure.com (env VISUAL_REVIEW_ENDPOINT)",
    )
    p.add_argument("--model", default=env("VISUAL_REVIEW_MODEL"), help="vision-capable deployment name (env VISUAL_REVIEW_MODEL)")
    p.add_argument("--max-images", type=int, default=12)
    p.add_argument("--out", type=Path, default=Path("visual-review.md"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None, client: Client | None = None) -> int:
    """Review the screenshots and write the markdown comment to `--out`; `--print-prompt` only prints the prompt.
    `--dry-run` prints the request. Always returns 0: the review is advisory."""
    args = parse_args(argv)
    if args.print_prompt:
        print(PROMPT)
        return 0
    require(args, "images", "endpoint", "model")
    images = collect_images(args.images, args.max_images)
    if not images:
        args.out.write_text(
            "<!-- visual-review -->\n## Visual review (advisory)\n\nNo screenshots were produced, nothing to review.\n"
        )
        return 0
    style_guide = args.style_guide.read_text(encoding="utf-8")
    client = client or Client(args.endpoint, FOUNDRY_SCOPE, dry_run=args.dry_run, timeout=180.0)
    request = build_request(args.model, style_guide, images, embed=not args.dry_run)
    response = client.request("POST", "/openai/v1/responses", body=request)
    if args.dry_run:
        return 0
    args.out.write_text(to_markdown(json.loads(output_text(response)), len(images)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
