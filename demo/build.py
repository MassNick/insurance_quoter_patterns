#!/usr/bin/env python3
"""Build the two standalone demo pages from demo/src.

    python3 demo/build.py

Inlines styles.css, the JS modules and contracts/state-catalog.json into
demo/journey.html and demo/form-gallery.html, so each page opens from disk
with no server and no network. Standard library only.
"""
import json
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
OUT = Path(__file__).resolve().parent
BOT_ICON = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
            'stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z"/>'
            '<path d="M9 12l2 2 4-4"/></svg>')
PAGES = {"journey.html": "journey.html", "gallery.html": "form-gallery.html"}


def safe_js(text: str) -> str:
    return text.replace("</script", "<\\/script")


def build(template: Path) -> str:
    html = template.read_text(encoding="utf-8")

    def inline_css(m):
        return (SRC / m.group(1)).read_text(encoding="utf-8")

    def inline_js(m):
        return "<script>\n" + safe_js((SRC / m.group(1)).read_text(encoding="utf-8")) + "\n</script>"

    def inline_json(m):
        name, rel = m.group(1), m.group(2)
        data = json.loads((SRC / rel).resolve().read_text(encoding="utf-8"))
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        return f"<script>\nwindow.{name} = {payload};\n</script>"

    html = re.sub(r"/\* @inline ([\w.-]+\.css) \*/", inline_css, html)
    html = re.sub(r"<script>/\* @inline-json (\w+) ([^ ]+) \*/</script>", inline_json, html)
    html = re.sub(r"<script>/\* @inline ([\w.-]+\.js) \*/</script>", inline_js, html)
    html = html.replace("__BOT_ICON__", BOT_ICON)
    if "@inline" in html:
        sys.exit(f"unresolved @inline marker in {template.name}")
    return html


def main() -> None:
    for src_name, out_name in PAGES.items():
        out = OUT / out_name
        out.write_text(build(SRC / src_name), encoding="utf-8")
        print(f"wrote {out.relative_to(OUT.parent)} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
