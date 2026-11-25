#!/usr/bin/env python3
"""Consistency checks for a quoting-agent design folder.

Run from the design folder (the one holding README.md, contracts/, demo/):

    python3 skill/scripts/check_artifacts.py            # all checks
    python3 skill/scripts/check_artifacts.py --tokens   # token budget only

Standard library only. Uses `node` (if installed) to read the FormSpecs from demo/src/forms.js.
Exits non-zero when a check fails.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
FAIL = []
WARN = []
TOKEN_CAP = 30000


def ok(msg):
    print(f"  ok    {msg}")


def fail(msg):
    FAIL.append(msg)
    print(f"  FAIL  {msg}")


def warn(msg):
    WARN.append(msg)
    print(f"  warn  {msg}")


def tok(text):
    return round(len(text) / 4)


def load_json(rel):
    try:
        return json.loads((ROOT / rel).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        fail(f"{rel}: invalid JSON ({e})")
        return None


def check_contracts():
    print("contracts")
    out = {}
    for rel in ["contracts/state-catalog.json", "contracts/tools.json", "contracts/form-spec.schema.json",
                "contracts/memory-output.schema.json", "contracts/document-output.schema.json"]:
        if (ROOT / rel).exists():
            d = load_json(rel)
            if d is not None:
                ok(f"{rel} parses")
                out[rel] = d
        else:
            warn(f"{rel} missing")
    cat = out.get("contracts/state-catalog.json")
    if cat:
        keys = [f["key"] for f in cat["fields"]]
        dup = {k for k in keys if keys.count(k) > 1}
        fail(f"duplicate catalog keys: {sorted(dup)}") if dup else ok(f"{len(keys)} unique catalog keys")
        bad = [f["key"] for f in cat["fields"] if f["entity"] not in cat["entities"]]
        fail(f"fields with unknown entity: {bad}") if bad else ok("every field has a known entity")
    return out


def token_budget(contracts):
    print("token budget (static prefix of the main agent, ~4 chars per token)")
    main = re.sub(r"<!--.*?-->", "", (ROOT / "prompts/main-agent.md").read_text(encoding="utf-8"), flags=re.S)
    tools = contracts.get("contracts/tools.json") or load_json("contracts/tools.json")
    fs = contracts.get("contracts/form-spec.schema.json") or load_json("contracts/form-spec.schema.json")
    cat = contracts.get("contracts/state-catalog.json") or load_json("contracts/state-catalog.json")
    t_tools = 0
    for t in tools["tools"]:
        schema = fs if t["inputSchema"].get("$ref") else t["inputSchema"]
        t_tools += tok(json.dumps({"name": t.get("wire_name", t["name"]), "description": t["description"], "parameters": schema}, separators=(",", ":")))
    keys = ";".join(f"{f['key']}:{f['type']}" + (("=" + "|".join(e[0] for e in f["enum"])) if isinstance(f.get("enum"), list) else "") for f in cat["fields"])
    t_main, t_keys = tok(main), tok(keys)
    total = t_main + t_tools + t_keys
    print(f"        main prompt {t_main:,} + tools {t_tools:,} + key list {t_keys:,} = {total:,} / {TOKEN_CAP:,}")
    fail(f"static prefix {total:,} tokens exceeds {TOKEN_CAP:,}") if total >= TOKEN_CAP else ok("under the 30k cap")
    return total


def read_forms():
    node = shutil.which("node")
    src = ROOT / "demo/src/forms.js"
    if not node or not src.exists():
        warn("node or demo/src/forms.js not found: FormSpec checks skipped")
        return None
    js = ("const vm=require('vm');const fs=require('fs');const w={};vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{window:w});"
          "process.stdout.write(JSON.stringify(w.FORMS))")
    r = subprocess.run([node, "-e", js, str(src)], capture_output=True, text=True, timeout=30)
    if r.returncode:
        fail(f"forms.js failed to evaluate: {r.stderr.strip()[:200]}")
        return None
    return json.loads(r.stdout)


def check_forms(contracts):
    print("forms (demo/src/forms.js)")
    forms = read_forms()
    if forms is None:
        return
    cat = contracts["contracts/state-catalog.json"]
    keys = {f["key"]: f for f in cat["fields"]}
    n = len(forms)
    fail(f"only {n} forms; the gallery needs at least 20") if n < 20 else ok(f"{n} forms (>= 20)")
    ids = [f["spec"]["form_id"] for f in forms]
    dup = {i for i in ids if ids.count(i) > 1}
    fail(f"duplicate form_id: {sorted(dup)}") if dup else ok("unique form_ids")
    widgets = set(contracts["contracts/form-spec.schema.json"]["$defs"]["field"]["properties"]["widget"]["enum"]) | {"section"}
    problems = []

    def walk(fields, in_list_repeater):
        for f in fields:
            w = f.get("widget", "auto")
            if w not in widgets:
                problems.append(f"unknown widget {w}")
            k = f.get("key")
            if k:
                last = k.rsplit("/", 1)[-1]
                if k.startswith("/"):
                    if last not in keys and not re.match(r"^/(quote|underwriting|vehicles|drivers|household)(/\d+)?(/\w+)?$", k):
                        problems.append(f"pointer {k}")
                    elif last not in keys and w not in ("repeater", "checkbox"):
                        problems.append(f"pointer {k} has no catalog key")
                elif last not in keys and not (in_list_repeater and w != "auto"):
                    problems.append(f"key {k} not in catalog")
            rep = f.get("repeat")
            if rep:
                coll = (k or "").rsplit("/", 1)[-1]
                is_list = coll in keys and keys[coll]["type"] == "list"
                walk(rep.get("fields", []), is_list)

    for f in forms:
        walk(f["spec"]["fields"], False)
    fail("FormSpec problems: " + "; ".join(sorted(set(problems)))) if problems else ok("every FormSpec key and widget resolves")
    det = {k for k, v in keys.items() if v.get("deterministic")}
    used = set()
    for f in forms:
        for fld in f["spec"]["fields"]:
            if fld.get("key"):
                used.add(fld["key"].rsplit("/", 1)[-1])
    missing = det - used
    warn(f"deterministic fields with no gallery form: {sorted(missing)}") if missing else ok("every deterministic field appears in a gallery form")


A2UI_JS = r"""
const vm=require('vm'),fs=require('fs');
const [a2,forms,cat]=process.argv.slice(1);
const w={STATE_CATALOG:JSON.parse(fs.readFileSync(cat,'utf8')),matchMedia:()=>({matches:false})};
const ctx={window:w,performance:{now:()=>0},setTimeout,console};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(a2,'utf8'),ctx);
vm.runInContext(fs.readFileSync(forms,'utf8'),ctx);
const ID=/^[A-Za-z_][A-Za-z0-9_]*$/;const out=[];
for(const f of w.FORMS){
  const msgs=w.A2UI.compile(f.spec);const p=[];
  if(msgs.length!==1||!msgs[0].createSurface)p.push('form must open with one createSurface');
  for(const m of msgs){ if(m.version!=='1.0')p.push('version '+m.version); }
  const cs=(msgs[0]||{}).createSurface||{};
  if(!ID.test(cs.surfaceId||''))p.push('surfaceId '+cs.surfaceId);
  if(!cs.components||!cs.dataModel)p.push('createSurface needs components and dataModel');
  if('theme' in cs)p.push('theme is invalid in 1.0');
  const ids=new Set((cs.components||[]).map(c=>c.id));
  if(!ids.has('root'))p.push('no root');
  const walk=(o,where)=>{ if(Array.isArray(o)){o.forEach(x=>walk(x,where));return;} if(!o||typeof o!=='object')return;
    const k=Object.keys(o);
    if(k.length===1&&k[0]==='path')p.push(where+': {path} binding (use @path)');
    if('call' in o)p.push(where+': call (use @call)');
    if('checks' in o)p.push(where+': checks (use @checks)');
    for(const key of k){ if(/^@/.test(key)&&!['@path','@call','@index','@checks','@catalogId','@accessibility','@metadata'].includes(key))p.push(where+': unknown directive '+key); walk(o[key],where); } };
  for(const c of cs.components||[]){
    if(!ID.test(c.id))p.push('component id '+c.id);
    if(c.component==='Text'&&/^h[1-6]$/.test(c.variant||''))p.push(c.id+': heading variant (use Markdown)');
    for(const ch of [].concat(c.children||[],c.child?[c.child]:[],c.template||[]))if(typeof ch==='string'&&!ids.has(ch))p.push(c.id+': missing child '+ch);
    for(const r of c['@checks']||[])if(!r.condition||!r.condition['@call'])p.push(c.id+': CheckRule without condition.@call');
    if(c.component==='Button'&&!(c.action&&c.action.event&&c.action.event.name))p.push(c.id+': Button without action.event.name');
    walk(c,c.id);
  }
  out.push([f.id,[...new Set(p)]]);
}
process.stdout.write(JSON.stringify(out));
"""


def check_a2ui():
    print("A2UI 1.0 conformance (every FormSpec compiled with demo/src/a2ui.js)")
    node = shutil.which("node")
    a2, forms, cat = ROOT / "demo/src/a2ui.js", ROOT / "demo/src/forms.js", ROOT / "contracts/state-catalog.json"
    if not node or not a2.exists():
        warn("node or demo/src/a2ui.js not found: A2UI check skipped")
        return
    r = subprocess.run([node, "-e", A2UI_JS, str(a2), str(forms), str(cat)], capture_output=True, text=True, timeout=60)
    if r.returncode:
        fail(f"A2UI compile failed: {r.stderr.strip()[:300]}")
        return
    bad = [(fid, probs) for fid, probs in json.loads(r.stdout) if probs]
    if bad:
        fail("A2UI 1.0 problems: " + "; ".join(f"{fid}: {', '.join(p[:3])}" for fid, p in bad[:6]))
    else:
        ok("all forms: version 1.0, one createSurface with components + dataModel, identifier ids, @path/@call/@checks, children resolve")


def check_tools_referenced(contracts):
    print("tool names used in docs and the process flow")
    names = {t["name"] for t in contracts["contracts/tools.json"]["tools"]}
    names |= {t["name"] for t in contracts["contracts/tools.json"].get("not_model_visible", [])}
    events_md = (ROOT / "contracts/events.md").read_text(encoding="utf-8") if (ROOT / "contracts/events.md").exists() else ""
    events = set(re.findall(r"`([a-z0-9_]+\.[a-z0-9_]+)`", events_md))
    events |= {"a2ui.compile"}  # OpenTelemetry span names that look like tools
    ns = r"(?:quoting|a2ui|ui|knowledge|document)\.[a-z_]+"
    unknown = {}
    for p in ROOT.glob("*.md"):
        for n in re.findall(rf"`({ns})(?:\(|`|\s)", p.read_text(encoding="utf-8")):
            if n not in names and n not in events:
                unknown.setdefault(n, set()).add(p.name)
    flow = ROOT / "process-flow.html"
    if flow.exists():
        m = re.search(r"/\* <flow-data> \*/(.*?)/\* </flow-data> \*/", flow.read_text(encoding="utf-8"), re.S)
        if m:
            for node in json.loads(m.group(1))["nodes"]:
                for item in (node.get("tools") or []) + (node.get("a2ui") or []):
                    mm = re.match(ns, item)
                    if mm and mm.group(0) not in names and mm.group(0) not in events:
                        unknown.setdefault(mm.group(0), set()).add("process-flow.html")
    if unknown:
        fail("unknown tool names: " + "; ".join(f"{k} ({', '.join(sorted(v))})" for k, v in sorted(unknown.items())))
    else:
        ok("every referenced tool exists in contracts/tools.json (event names excluded)")
    flow = ROOT / "process-flow.html"
    if flow.exists():
        m = re.search(r"/\* <flow-data> \*/(.*?)/\* </flow-data> \*/", flow.read_text(encoding="utf-8"), re.S)
        if m:
            data = json.loads(m.group(1))
            ids = {n["id"] for n in data["nodes"]}
            bad = [e for e in data["edges"] if e[0] not in ids or e[1] not in ids]
            fail(f"process-flow edges with unknown nodes: {bad[:5]}") if bad else ok(f"process flow: {len(ids)} nodes, {len(data['edges'])} edges, all resolve")


def check_readme_deterministic(contracts):
    print("README deterministic list vs catalog")
    det = {f["key"] for f in contracts["contracts/state-catalog.json"]["fields"] if f.get("deterministic")}
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    row = next((line for line in readme.splitlines() if "Deterministic fields" in line), "")
    listed = set(re.findall(r"`([a-z_]+)`", row)) - {"deterministic"}
    listed = {k for k in listed if k in {f["key"] for f in contracts["contracts/state-catalog.json"]["fields"]}}
    fail(f"README lists {sorted(listed)}, catalog has {sorted(det)}") if listed != det else ok(f"{len(det)} deterministic fields match")


SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9_-]{20,}", "OpenAI-style key"),
    (r"AccountKey=[A-Za-z0-9+/=]{20,}", "storage account key"),
    (r"InstrumentationKey=[0-9a-f]{8}-[0-9a-f]{4}-", "App Insights connection string"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"AKIA[0-9A-Z]{16}", "AWS key"),
    (r"xox[abp]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"(?i)(api[_-]?key|secret|password)\s*[:=]\s*[\"'][A-Za-z0-9/+_-]{16,}[\"']", "inline secret"),
    (r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.", "JWT"),
]


def check_secrets():
    print("secrets")
    hits = []
    skip = {".venv", "node_modules", ".terraform", "__pycache__", "test-results", "playwright-report", "dist"}
    for p in ROOT.rglob("*"):
        if p.is_dir() or skip.intersection(p.relative_to(ROOT).parts):
            continue  # installed dependencies and build output are not ours to scan
        if p.suffix not in {".md", ".json", ".js", ".ts", ".tsx", ".tf", ".tfvars", ".yml", ".yaml", ".html", ".py", ".drawio", ".css", ".txt", ".sh", ".env"}:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat, what in SECRET_PATTERNS:
            if re.search(pat, text):
                hits.append(f"{p.relative_to(ROOT)}: {what}")
    fail("possible secrets: " + "; ".join(hits)) if hits else ok("no secret patterns found")


def check_built_pages():
    print("built demo pages")
    for rel in ["demo/journey.html", "demo/form-gallery.html"]:
        p = ROOT / rel
        if not p.exists():
            fail(f"{rel} missing: run python3 demo/build.py")
            continue
        text = p.read_text(encoding="utf-8")
        issues = []
        if "@inline" in text or "__BOT_ICON__" in text:
            issues.append("unresolved build markers")
        if re.search(r"<script[^>]+src=|<link[^>]+href=[\"']https?:|\bfetch\(|XMLHttpRequest|new WebSocket", text):
            issues.append("network reference")
        fail(f"{rel}: {', '.join(issues)}") if issues else ok(f"{rel}: standalone ({p.stat().st_size // 1024} KB), no network")
    flow = ROOT / "process-flow.html"
    if flow.exists() and re.search(r"<script[^>]+src=|<link[^>]+href=[\"']https?:|\bfetch\(", flow.read_text(encoding="utf-8")):
        fail("process-flow.html references the network")


def main():
    if not (ROOT / "contracts").is_dir():
        sys.exit("run from the design folder (the one with contracts/)")
    contracts = check_contracts()
    token_budget(contracts)
    if "--tokens" in sys.argv:
        return
    check_forms(contracts)
    check_a2ui()
    check_tools_referenced(contracts)
    check_readme_deterministic(contracts)
    check_secrets()
    check_built_pages()
    print(f"\n{len(FAIL)} failure(s), {len(WARN)} warning(s)")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
