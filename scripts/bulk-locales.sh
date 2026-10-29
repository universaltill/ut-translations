#!/usr/bin/env bash
# Translate and approve whole languages in Weblate, in a Docker Desktop
# container, one language after the other (ut-docs#4371).
#
#   scripts/bulk-locales.sh fr es pt            # Windows GPU Ollama (default)
#   scripts/bulk-locales.sh --nas zh_Hans ms    # NAS Ollama (slower, always on)
#   scripts/bulk-locales.sh --approve-only fa ar tr   # no translating, just approve
#
# Then:  docker logs -f ut-bulk-locales     reports: ~/ut-bulk-reports/<lang>.json
#
# Tokens come from your Keychain (weblate-translator, weblate-bootstrap-approver)
# and, with --nas, the Key Vault; they reach the container as inherited
# environment variables, never on a command line or in a file.
set -euo pipefail
cd "$(dirname "$0")/.."

cmd=all
url="${OLLAMA_URL:-http://192.168.1.118:11434}"
model="${OLLAMA_MODEL:-qwen3.8:27b}"
nas=0
while [ $# -gt 0 ]; do
  case "$1" in
    --nas) nas=1; url="http://192.168.1.231:11434"; model="${OLLAMA_MODEL:-qwen3:30b-a3b}"; shift ;;
    --approve-only) cmd=approve; shift ;;
    --) shift; break ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
[ $# -gt 0 ] || { echo "give at least one language code, e.g. fr es zh_Hans" >&2; exit 2; }

kc() { security find-generic-password -a "$USER" -s "$1" -w; }
WEBLATE_APPROVER_TOKEN="$(kc weblate-bootstrap-approver)"
WEBLATE_TRANSLATOR_TOKEN="$(kc weblate-translator)"
export WEBLATE_APPROVER_TOKEN WEBLATE_TRANSLATOR_TOKEN
if [ "$nas" = 1 ]; then
  OLLAMA_API_TOKEN="$(AZURE_CONFIG_DIR="$HOME/.azure-unitill" az keyvault secret show \
    --vault-name kv-unitill-dev --name ollama-api-token --query value -o tsv)"
  export OLLAMA_API_TOKEN
fi

mkdir -p "$HOME/ut-bulk-reports"
docker rm -f ut-bulk-locales >/dev/null 2>&1 || true
docker run -d --name ut-bulk-locales \
  -e WEBLATE_APPROVER_TOKEN -e WEBLATE_TRANSLATOR_TOKEN -e OLLAMA_API_TOKEN \
  -e OLLAMA_URL="$url" -e OLLAMA_MODEL="$model" -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONUNBUFFERED=1 \
  -v "$PWD:/work:ro" -v "$HOME/ut-bulk-reports:/reports" \
  python:3.13-slim sh -c '
    cmd="$1"; shift
    for l in "$@"; do
      echo "=== $l ($cmd) $(date -u +%H:%M:%S)"
      python /work/tools/bulk_locale.py "$cmd" --locale "$l" --include-approved \
        --report "/reports/$l.json" || echo "!!! $l stopped, see its report"
    done
    echo "=== all done $(date -u +%H:%M:%S)"' sh "$cmd" "$@"
echo "started: docker logs -f ut-bulk-locales   (reports in ~/ut-bulk-reports)"
