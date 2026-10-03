#!/usr/bin/env bash
# Follow GET /runs/stream and pretty-print each server-sent event through jq as it arrives.
#   scripts/stream.sh "Plan my week"            API_URL=http://localhost:8765 scripts/stream.sh
# curl -N turns off curl's output buffering, or the events arrive in one lump at the end.
# jq -R reads raw lines, keeps only the `data:` ones, and parses the JSON after the prefix;
# --unbuffered prints each event the moment it is parsed. JQ_FLAGS=-c for one line per event.
# Ctrl-C closes the connection, which is the disconnect that cancels the run on the server.
set -euo pipefail

command -v jq >/dev/null || { echo "stream.sh needs jq (brew install jq)" >&2; exit 2; }

curl -sSN --get --data-urlencode "task=${1:-Plan my week}" "${API_URL:-http://localhost:8000}/runs/stream" \
  | jq -R --unbuffered ${JQ_FLAGS:-} 'select(startswith("data: ")) | .[6:] | fromjson'
