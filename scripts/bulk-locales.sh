#!/usr/bin/env bash
# Translate and approve whole languages in Weblate, in a Docker Desktop
# container, one language after the other (ut-docs#4371).
#
#   scripts/bulk-locales.sh fr es pt            # Windows GPU Ollama (default)
#   scripts/bulk-locales.sh --nas zh_Hans ms    # NAS Ollama (slower, always on)
#   scripts/bulk-locales.sh --approve-only fa ar tr   # no translating, just approve
#   scripts/bulk-locales.sh --legal --name ut-bulk-legal de   # legal strings, approved by YOU
#
# --name NAME   container name (default ut-bulk-locales, or ut-bulk-legal with
#               --legal). A running container of that name is never replaced.
#
# Then:  docker logs -f ut-bulk-locales     reports: ~/ut-bulk-reports/<lang>-<command>.json, log: ~/ut-bulk-reports/<name>.log
#
# Tokens come from your Keychain (weblate-translator, weblate-bootstrap-approver,
# and with --legal your own weblate-farshid)
# and, with --nas, the Key Vault; they reach the container as inherited
# environment variables, never on a command line or in a file.
set -euo pipefail
cd "$(dirname "$0")/.."

cmd=all
url="${OLLAMA_URL:-http://192.168.1.118:11434}"
model="${OLLAMA_MODEL:-qwen3.8:27b}"
nas=0
name_set=
while [ $# -gt 0 ]; do
  case "$1" in
    --nas) nas=1; url="http://192.168.1.231:11434"; model="${OLLAMA_MODEL:-qwen3:30b-a3b}"; shift ;;
    --approve-only) cmd=approve; shift ;;
    --legal) cmd=approve-legal; shift ;;
    --name) [ $# -ge 2 ] && [ "${2#-}" = "$2" ] || { echo "--name needs a name" >&2; exit 2; }; name_set="$2"; shift 2 ;;
    --) shift; break ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
name="${name_set:-$([ "$cmd" = approve-legal ] && echo ut-bulk-legal || echo ut-bulk-locales)}"
[ $# -gt 0 ] || { echo "give at least one language code, e.g. fr es zh_Hans" >&2; exit 2; }

kc() { security find-generic-password -a "$USER" -s "$1" -w; }
WEBLATE_APPROVER_TOKEN="$(kc weblate-bootstrap-approver)"
WEBLATE_TRANSLATOR_TOKEN="$(kc weblate-translator)"
export WEBLATE_APPROVER_TOKEN WEBLATE_TRANSLATOR_TOKEN
if [ "$cmd" = approve-legal ]; then
  WEBLATE_HUMAN_TOKEN="$(kc weblate-farshid)"
  export WEBLATE_HUMAN_TOKEN
fi
if [ "$nas" = 1 ]; then
  OLLAMA_API_TOKEN="$(AZURE_CONFIG_DIR="$HOME/.azure-unitill" az keyvault secret show \
    --vault-name kv-unitill-dev --name ollama-api-token --query value -o tsv)"
  export OLLAMA_API_TOKEN
fi

mkdir -p "$HOME/ut-bulk-reports"
if [ -n "$(docker ps -q -f "name=^${name}$")" ]; then
  echo "container $name is still running; give --name another name or wait for it" >&2; exit 1
fi
docker rm -f "$name" >/dev/null 2>&1 || true
human_env=()
[ "$cmd" = approve-legal ] && human_env=(-e WEBLATE_HUMAN_TOKEN)
docker run -d --rm --name "$name" \
  -e WEBLATE_APPROVER_TOKEN -e WEBLATE_TRANSLATOR_TOKEN -e OLLAMA_API_TOKEN ${human_env[@]+"${human_env[@]}"} \
  -e BULK_NAME="$name" -e OLLAMA_URL="$url" -e OLLAMA_MODEL="$model" -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONUNBUFFERED=1 \
  -v "$PWD:/work:ro" -v "$HOME/ut-bulk-reports:/reports" \
  python:3.13-slim sh -c '
    cmd="$1"; shift
    { for l in "$@"; do
        echo "=== $l ($cmd) $(date -u +%H:%M:%S)"
        python /work/tools/bulk_locale.py "$cmd" --locale "$l" --include-approved \
          --report "/reports/$l-$cmd.json" || echo "!!! $l stopped, see its report"
      done
      echo "=== all done $(date -u +%H:%M:%S)"; } 2>&1 | tee -a "/reports/$BULK_NAME.log"' sh "$cmd" "$@"
echo "started: docker logs -f $name   (reports in ~/ut-bulk-reports)"
