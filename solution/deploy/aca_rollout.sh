#!/usr/bin/env bash
# Revision rollout of one container app: new revision at 0%, wait until healthy, shift traffic, promote or roll back.
# The apps run in revision mode Multiple (infra/terraform/apps.tf), which is what makes the traffic split possible.
set -euo pipefail

# Print the usage text (the caller sends it to stdout for -h and to stderr after a usage error).
usage() {
  cat <<USAGE
Usage:
  aca_rollout.sh [--dry-run] release  -g RESOURCE_GROUP APP IMAGE [CANARY_PERCENT]   prints "<previous> <new>" on stdout
  aca_rollout.sh [--dry-run] finish   -g RESOURCE_GROUP APP NEW PREVIOUS               NEW takes 100%, PREVIOUS is deactivated
  aca_rollout.sh [--dry-run] rollback -g RESOURCE_GROUP APP NEW PREVIOUS               PREVIOUS takes 100%, NEW is deactivated

release pins the traffic on the current revision, creates a revision for IMAGE (0% traffic), waits for it to be healthy (10 min),
then gives it CANARY_PERCENT (default 100). When the app already runs IMAGE it prints the same revision twice and does nothing.
Run the smoke tests, then finish or rollback. mcp-quoting keeps its quotes in memory:
release it with 100 (a split would send modify_quote to a revision that does not know the quote) and expect open quotes to be lost.
Rollback after a finish: the same rollback command, or: az containerapp ingress traffic set -n APP -g RG --revision-weight PREVIOUS=100
(the previous revision stays inactive but available until it is deleted).
USAGE
}

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && { DRY_RUN=1; shift; }
CMD="${1:-}"; shift || true
case "$CMD" in -h|--help|"") usage; exit 0 ;; release|finish|rollback) ;; *) usage >&2; exit 2 ;; esac
[ "${1:-}" = "-g" ] || { usage >&2; exit 2; }
RG="${2:?resource group}"; APP="${3:?app}"; shift 3

# Run a command; with --dry-run only print it (to stderr).
run() { if [ "$DRY_RUN" = 1 ]; then printf '+ %s\n' "$*" >&2; else "$@"; fi; }
# Set the traffic split of the app: the arguments are REVISION=PERCENT pairs.
weights() { run az containerapp ingress traffic set -n "$APP" -g "$RG" --revision-weight "$@" >&2; }

# release IMAGE [CANARY_PERCENT]: new revision at 0%, wait until it is healthy, then give it the canary share (default 100);
# prints "<previous> <new>" on stdout, or the same revision twice when the app already runs IMAGE.
release() {
  local image="${1:?image}" canary="${2:-100}" current new
  if [ "$DRY_RUN" = 1 ]; then current="<current>"; else
    current="$(az containerapp revision list -n "$APP" -g "$RG" --query "[?properties.trafficWeight>\`0\`].name | [0]" -o tsv)"
  fi
  if [ "$DRY_RUN" = 0 ] && [ "$(az containerapp revision show -n "$APP" -g "$RG" --revision "$current" --query 'properties.template.containers[0].image' -o tsv)" = "$image" ]; then
    echo "$APP already runs $image" >&2
    echo "$current $current"
    return
  fi
  new="${APP}--r$(date -u +%m%d%H%M%S)"
  weights "$current=100"                                   # stop following the latest revision
  run az containerapp update -n "$APP" -g "$RG" --image "$image" --revision-suffix "${new#"$APP"--}" >&2
  if [ "$DRY_RUN" = 0 ]; then
    for _ in $(seq 1 60); do
      [ "$(az containerapp revision show -n "$APP" -g "$RG" --revision "$new" --query properties.healthState -o tsv)" = Healthy ] && break
      sleep 10
    done
    [ "$(az containerapp revision show -n "$APP" -g "$RG" --revision "$new" --query properties.healthState -o tsv)" = Healthy ] \
      || { echo "revision $new is not healthy; traffic stays on $current" >&2; exit 1; }
  fi
  if [ "$canary" -ge 100 ]; then weights "$new=100"; else weights "$new=$canary" "$current=$((100 - canary))"; fi
  echo "$current $new"
}

[ "$CMD" != release ] && [ "${1:-}" = "${2:-}" ] && { echo "$APP: nothing to $CMD (same revision)" >&2; exit 0; }
case "$CMD" in
  release) release "$@" ;;
  finish) weights "$1=100"; run az containerapp revision deactivate -n "$APP" -g "$RG" --revision "$2" >&2 ;;
  rollback) weights "$2=100"; run az containerapp revision deactivate -n "$APP" -g "$RG" --revision "$1" >&2 ;;
esac
