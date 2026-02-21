#!/usr/bin/env bash
set -euo pipefail

WATCH_PATH="${WATCH_PATH:-/watch}"
GSHARE_EVENT_URL="${GSHARE_EVENT_URL:-}"
EVENT_AUTH_TOKEN="${EVENT_AUTH_TOKEN:-}"

if [[ -z "$GSHARE_EVENT_URL" ]]; then
  echo "GSHARE_EVENT_URL is required" >&2
  exit 1
fi

if [[ ! -d "$WATCH_PATH" ]]; then
  echo "WATCH_PATH does not exist: $WATCH_PATH" >&2
  exit 1
fi

echo "Watching $WATCH_PATH"

inotifywait -m -r -e close_write -e moved_to -e create --format '%w%f' "$WATCH_PATH" | while read -r changed; do
  if [[ -d "$changed" ]]; then
    folder="$changed"
  else
    folder="$(dirname "$changed")"
  fi

  rel="${folder#${WATCH_PATH}/}"
  if [[ "$rel" == "$folder" ]]; then
    rel="."
  fi

  payload="{\"folder\":\"${rel//\"/\\\"}\"}"
  if [[ -n "$EVENT_AUTH_TOKEN" ]]; then
    curl -fsS -X POST "$GSHARE_EVENT_URL" \
      -H 'Content-Type: application/json' \
      -H "X-GShare-Token: $EVENT_AUTH_TOKEN" \
      -d "$payload" >/dev/null || true
  else
    curl -fsS -X POST "$GSHARE_EVENT_URL" \
      -H 'Content-Type: application/json' \
      -d "$payload" >/dev/null || true
  fi

done
