#!/usr/bin/env bash
# Every offline suite in one go, the same checks as CI (.github/workflows/ci.yml) minus Terraform and containers.
#
#   ./test_all.sh            unit + component + lint for every Python component, web typecheck/lint/unit/visual/e2e,
#                            API end-to-end journeys
#   ./test_all.sh --quick    Python unit + component + lint, web typecheck/lint/unit only
#   ./test_all.sh --full     everything above plus the full-stack browser journeys (web/e2e-stack)
#
# Needs each component's .venv and web/node_modules (see each README). Never calls Azure.
set -uo pipefail
cd "$(dirname "$0")"
mode="${1:-}"
failed=()

run() {  # run <label> <command...>
  local label="$1"; shift
  printf '\n== %s\n' "$label"
  if "$@"; then echo "ok: $label"; else failed+=("$label"); echo "FAILED: $label"; fi
}

for dir in packages/quote_common mcp/a2ui mcp/quoting bff agents/quote-agent agents/document-agent deploy; do
  run "$dir: ruff" uvx ruff check "$dir"
  run "$dir: pytest" bash -c "cd '$dir' && .venv/bin/python -m pytest -q -p no:cacheprovider"
done

run "web: typecheck" npm --prefix web run --silent typecheck
run "web: lint" npm --prefix web run --silent lint
run "web: unit + component" npm --prefix web test --silent
if [[ "$mode" != "--quick" ]]; then
  run "web: visual" npm --prefix web run --silent test:visual
  run "web: e2e (mock BFF)" npm --prefix web run --silent test:e2e
  run "e2e: API journeys" bff/.venv/bin/python -m pytest -c e2e/pytest.ini e2e
fi
if [[ "$mode" == "--full" ]]; then
  run "e2e: full-stack browser" npm --prefix web run --silent test:stack
fi

printf '\n'
if ((${#failed[@]})); then printf 'FAILED (%d):\n' "${#failed[@]}"; printf '  - %s\n' "${failed[@]}"; exit 1; fi
echo "all suites passed"
