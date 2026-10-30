#!/usr/bin/env bash
# Fill empty Weblate strings from the old language plugins, then approve them
# (ut-docs#4371). Needs the plugin checkouts next to this repo.
#
#   scripts/fill-from-plugins.sh de es pt
#   scripts/fill-from-plugins.sh --dry-run de
#
# Tokens come from your Keychain (weblate-translator, weblate-bootstrap-approver);
# reports go to ~/ut-bulk-reports/<lang>-fill.json.
set -euo pipefail
cd "$(dirname "$0")/.."

extra=(--approve)
langs=()
for a in "$@"; do
  case "$a" in
    --dry-run) extra=(--dry-run) ;;
    -*) echo "unknown option $a" >&2; exit 2 ;;
    *) langs+=("$a") ;;
  esac
done
[ ${#langs[@]} -gt 0 ] || { echo "give at least one language code, e.g. de es pt" >&2; exit 2; }

mkdir -p "$HOME/ut-bulk-reports"
rc=0
for l in "${langs[@]}"; do
  dir="../ut-plugin-language-$(printf '%s' "$l" | tr '_A-Z' '-a-z')"
  [ -d "$dir/locales" ] || { echo "no plugin checkout for $l at $dir" >&2; continue; }
  git -C "$dir" pull -q --ff-only || echo "warning: $dir not fast-forwarded, using what is there" >&2
  echo "=== $l"
  python3 -I tools/fill_from_plugin.py --locale "$l" --plugin "$dir" "${extra[@]}" \
    --report "$HOME/ut-bulk-reports/$l-fill.json" || { echo "!!! $l failed, going on with the next" >&2; rc=1; }
done
exit $rc
