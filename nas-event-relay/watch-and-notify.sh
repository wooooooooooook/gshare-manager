#!/usr/bin/env bash
set -euo pipefail

WATCH_PATH="${WATCH_PATH:-/watch}"
GSHARE_EVENT_URL="${GSHARE_EVENT_URL:-}"
EVENT_AUTH_TOKEN="${EVENT_AUTH_TOKEN:-}"
GSHARE_HEALTH_URL="${GSHARE_HEALTH_URL:-}"
HEALTH_INTERVAL_SECONDS="${HEALTH_INTERVAL_SECONDS:-30}"
# 쉼표(,)로 구분된 디렉토리 이름/패턴 목록.
# 기본값은 Synology 메타데이터 폴더(@eaDir) + '@'/'dot' 접두 폴더 전체 제외.
EXCLUDED_DIR_NAMES="${EXCLUDED_DIR_NAMES:-@eaDir,@*,.*}"

if [[ -z "$GSHARE_EVENT_URL" ]]; then
  echo "GSHARE_EVENT_URL is required" >&2
  exit 1
fi

if [[ -z "$GSHARE_HEALTH_URL" ]]; then
  GSHARE_HEALTH_URL="${GSHARE_EVENT_URL%/api/folder-event}/api/event-relay/health"
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
  local patterns_csv="$2"
  local IFS=','
  local rel segment pattern
  local -a patterns

  rel="${target#${WATCH_PATH}/}"
  if [[ "$rel" == "$target" ]]; then
    rel="${target#/}"
  fi

  read -ra patterns <<< "$patterns_csv"
  IFS='/' read -ra segments <<< "$rel"

  for segment in "${segments[@]}"; do
    [[ -z "$segment" || "$segment" == "." ]] && continue

    for pattern in "${patterns[@]}"; do
      pattern="${pattern//[[:space:]]/}"
      [[ -z "$pattern" ]] && continue

      if [[ "$segment" == $pattern ]]; then
        return 0
      fi
    done
  done

  return 1
}


post_health() {
  local payload http_code
  payload='{"status":"alive"}'

  if [[ -n "$EVENT_AUTH_TOKEN" ]]; then
    http_code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$GSHARE_HEALTH_URL" \
      --connect-timeout 2 --max-time 5 \
      -H 'Content-Type: application/json' \
      -H "X-GShare-Token: $EVENT_AUTH_TOKEN" \
      -d "$payload") || return 1
  else
    http_code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$GSHARE_HEALTH_URL" \
      --connect-timeout 2 --max-time 5 \
      -H 'Content-Type: application/json' \
      -d "$payload") || return 1
  fi

  [[ "$http_code" =~ ^2[0-9][0-9]$ ]]
}

start_health_loop() {
  (
    while true; do
      if ! post_health; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [warn] health notify failed url=$GSHARE_HEALTH_URL" >&2
      fi
      sleep "$HEALTH_INTERVAL_SECONDS"
    done
  ) &
  HEALTH_LOOP_PID=$!
}

cleanup() {
  if [[ -n "${HEALTH_LOOP_PID:-}" ]]; then
    kill "$HEALTH_LOOP_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM


count_watch_targets() {
  local total=0 included=0
  local dir

  while IFS= read -r dir; do
    [[ -z "$dir" ]] && continue
    total=$((total + 1))

    if ! is_excluded_path "$dir" "$EXCLUDED_DIR_NAMES"; then
      included=$((included + 1))
    fi
  done < <(find "$WATCH_PATH" -type d 2>/dev/null)

  echo "$total|$included"
}

post_event() {
  local folder="$1"
  local payload http_code curl_exit attempt max_attempts
  payload="{\"folder\":\"${folder//\"/\\\"}\"}"
  max_attempts=3

  for attempt in $(seq 1 "$max_attempts"); do
    if [[ -n "$EVENT_AUTH_TOKEN" ]]; then
      http_code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$GSHARE_EVENT_URL" \
        --connect-timeout 2 --max-time 5 \
        -H 'Content-Type: application/json' \
        -H "X-GShare-Token: $EVENT_AUTH_TOKEN" \
        -d "$payload")
      curl_exit=$?
    else
      http_code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$GSHARE_EVENT_URL" \
        --connect-timeout 2 --max-time 5 \
        -H 'Content-Type: application/json' \
        -d "$payload")
      curl_exit=$?
    fi

    if [[ $curl_exit -eq 0 && "$http_code" =~ ^2[0-9][0-9]$ ]]; then
      return 0
    fi

    if [[ $attempt -lt $max_attempts ]]; then
      sleep 1
    fi
  done

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [warn] notify request failed folder=$folder curl_exit=$curl_exit http_code=${http_code:-000}" >&2
  return 1
}

watch_counts="$(count_watch_targets)"
watch_total="${watch_counts%%|*}"
watch_included="${watch_counts##*|}"

echo "Watching recursively: $WATCH_PATH (excluding: $EXCLUDED_DIR_NAMES, fs: $FS_TYPE, watch_dirs_total: $watch_total, watch_dirs_effective: $watch_included)"

watch_counts="$(count_watch_targets)"
watch_total="${watch_counts%%|*}"
watch_included="${watch_counts##*|}"
echo "Watch target summary: watch_dirs_total=$watch_total watch_dirs_effective=$watch_included"
start_health_loop
echo "Health check loop started: every ${HEALTH_INTERVAL_SECONDS}s -> $GSHARE_HEALTH_URL"

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
