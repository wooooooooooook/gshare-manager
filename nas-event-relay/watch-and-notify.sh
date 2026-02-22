#!/usr/bin/env bash
set -euo pipefail

WATCH_PATH="${WATCH_PATH:-/watch}"
GSHARE_EVENT_URL="${GSHARE_EVENT_URL:-}"
EVENT_AUTH_TOKEN="${EVENT_AUTH_TOKEN:-}"
# 쉼표(,)로 구분된 디렉토리 이름 목록. 기본값은 Synology 메타데이터 폴더.
EXCLUDED_DIR_NAMES="${EXCLUDED_DIR_NAMES:-@eaDir}"

if [[ -z "$GSHARE_EVENT_URL" ]]; then
  echo "GSHARE_EVENT_URL is required" >&2
  exit 1
fi

if [[ ! -d "$WATCH_PATH" ]]; then
  echo "WATCH_PATH does not exist: $WATCH_PATH" >&2
  exit 1
fi

FS_TYPE="$(stat -f -c %T "$WATCH_PATH" 2>/dev/null || echo unknown)"

case "$FS_TYPE" in
  nfs|cifs|smb2|fuseblk|fuse)
    echo "[warn] WATCH_PATH filesystem type is '$FS_TYPE'."
    echo "[warn] inotify may miss events created outside this kernel context (e.g. remote/network writes)."
    ;;
esac

is_excluded_path() {
  local target="$1"
  local names_csv="$2"
  local IFS=','

  read -ra names <<< "$names_csv"
  for name in "${names[@]}"; do
    # 공백 제거
    name="${name//[[:space:]]/}"
    [[ -z "$name" ]] && continue

    if [[ "$target" == *"/$name" || "$target" == *"/$name/"* ]]; then
      return 0
    fi
  done

  return 1
}

post_event() {
  local folder="$1"
  local payload
  payload="{\"folder\":\"${folder//\"/\\\"}\"}"

  if [[ -n "$EVENT_AUTH_TOKEN" ]]; then
    curl -fsS -X POST "$GSHARE_EVENT_URL" \
      -H 'Content-Type: application/json' \
      -H "X-GShare-Token: $EVENT_AUTH_TOKEN" \
      -d "$payload" >/dev/null
  else
    curl -fsS -X POST "$GSHARE_EVENT_URL" \
      -H 'Content-Type: application/json' \
      -d "$payload" >/dev/null
  fi
}

echo "Watching recursively: $WATCH_PATH (excluding: $EXCLUDED_DIR_NAMES, fs: $FS_TYPE)"

inotifywait -m -r \
  -e close_write -e moved_to -e create \
  --format '%e|%w%f' "$WATCH_PATH" | while IFS='|' read -r event changed; do
  if is_excluded_path "$changed" "$EXCLUDED_DIR_NAMES"; then
    continue
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] event=$event path=$changed"

  if [[ -d "$changed" ]]; then
    folder="$changed"
  else
    folder="$(dirname "$changed")"
  fi

  if is_excluded_path "$folder" "$EXCLUDED_DIR_NAMES"; then
    continue
  fi

  rel="${folder#${WATCH_PATH}/}"
  if [[ "$rel" == "$folder" ]]; then
    rel="."
  fi

  if post_event "$rel"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] notified folder=$rel"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [warn] notify failed folder=$rel"
  fi
done
