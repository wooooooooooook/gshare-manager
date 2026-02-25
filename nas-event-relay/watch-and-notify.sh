#!/usr/bin/env bash
set -euo pipefail

WATCH_PATH="${WATCH_PATH:-/watch}"
GSHARE_EVENT_URL="${GSHARE_EVENT_URL:-}"
EVENT_AUTH_TOKEN="${EVENT_AUTH_TOKEN:-}"
# 쉼표(,)로 구분된 디렉토리 이름/패턴 목록.
# 기본값은 Synology 메타데이터 폴더(@eaDir) + '@'/'dot' 접두 폴더 전체 제외.
EXCLUDED_DIR_NAMES="${EXCLUDED_DIR_NAMES:-@eaDir,@*,.*}"
WATCHLIST_REFRESH_INTERVAL_SECONDS="${WATCHLIST_REFRESH_INTERVAL_SECONDS:-86400}"
HEARTBEAT_INTERVAL_SECONDS="${HEARTBEAT_INTERVAL_SECONDS:-30}"

if [[ -z "$GSHARE_EVENT_URL" ]]; then
  echo "GSHARE_EVENT_URL is required" >&2
  exit 1
fi

if [[ ! -d "$WATCH_PATH" ]]; then
  echo "WATCH_PATH does not exist: $WATCH_PATH" >&2
  exit 1
fi

detect_fs_type() {
  local fs_type mount_source

  fs_type="$(stat -f -c %T "$WATCH_PATH" 2>/dev/null || true)"
  if [[ -n "$fs_type" && "$fs_type" != "?" ]]; then
    echo "$fs_type"
    return 0
  fi

  mount_source="$(awk -v path="$WATCH_PATH" '
    BEGIN {
      best_len = -1
      best_fs = ""
    }
    {
      mount = $2
      gsub("\\\\040", " ", mount)
      if (index(path, mount) == 1) {
        mount_len = length(mount)
        if (mount_len > best_len) {
          best_len = mount_len
          best_fs = $3
        }
      }
    }
    END {
      if (best_fs != "") {
        print best_fs
      }
    }
  ' /proc/mounts 2>/dev/null || true)"

  if [[ -n "$mount_source" ]]; then
    echo "$mount_source"
    return 0
  fi

  echo "unknown"
}

FS_TYPE="$(detect_fs_type)"
WATCHLIST_FILE="$(mktemp)"
EVENT_PIPE="$(mktemp -u /tmp/nas-event-relay.pipe.XXXXXX)"
LAST_WATCHLIST_REFRESH_EPOCH=0
PENDING_REFRESH=0

cleanup() {
  exec 3>&- || true
  exec 4>&- || true
  rm -f "$WATCHLIST_FILE"
  rm -f "$EVENT_PIPE"
}
trap cleanup EXIT

read_inotify_limit() {
  local key="$1"
  local path="/proc/sys/fs/inotify/$key"
  if [[ -r "$path" ]]; then
    cat "$path"
    return 0
  fi

  echo "unknown"
}

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

build_watchlist_file() {
  local IFS=','
  local pattern
  local include_count
  local -a patterns
  local -a find_cmd

  : > "$WATCHLIST_FILE"

  read -ra patterns <<< "$EXCLUDED_DIR_NAMES"
  find_cmd=(find "$WATCH_PATH")

  local has_pattern=0
  for pattern in "${patterns[@]}"; do
    pattern="${pattern//[[:space:]]/}"
    [[ -z "$pattern" ]] && continue

    if [[ $has_pattern -eq 0 ]]; then
      find_cmd+=( "(" -name "$pattern" )
      has_pattern=1
    else
      find_cmd+=( -o -name "$pattern" )
    fi
  done

  if [[ $has_pattern -eq 1 ]]; then
    find_cmd+=( ")" -prune -o -type d -print )
  else
    find_cmd+=( -type d -print )
  fi

  "${find_cmd[@]}" > "$WATCHLIST_FILE" 2>/dev/null
  include_count="$(wc -l < "$WATCHLIST_FILE" | tr -d '[:space:]')"
  echo "$include_count"
}

refresh_watch_targets() {
  local reason="$1"
  local watch_included
  local watch_sample

  watch_included="$(build_watchlist_file)"
  LAST_WATCHLIST_REFRESH_EPOCH="$(date +%s)"
  PENDING_REFRESH=0
  watch_sample="$(head -n 5 "$WATCHLIST_FILE" | tr '\n' ';')"

  echo "Watching directory list: $WATCH_PATH (excluding: $EXCLUDED_DIR_NAMES, fs: $FS_TYPE, watch_dirs_effective: $watch_included, refresh_reason: $reason)"
  echo "Watch target summary: watch_dirs_effective=$watch_included"
  echo "[watch-register] reason=$reason step=watchlist-built watchlist_file=$WATCHLIST_FILE entries=$watch_included"
  if [[ -n "$watch_sample" ]]; then
    echo "[watch-register] reason=$reason step=watchlist-sample sample=${watch_sample%;}"
  fi
}

should_refresh_now() {
  local now
  now="$(date +%s)"
  (( now - LAST_WATCHLIST_REFRESH_EPOCH >= WATCHLIST_REFRESH_INTERVAL_SECONDS ))
}

mark_refresh_if_needed() {
  local changed="$1"

  if should_refresh_now; then
    echo "[info] new directory detected. refreshing watch target list now."
    return 0
  fi

  if [[ $PENDING_REFRESH -eq 0 ]]; then
    echo "[info] new directory detected. watch target list refresh is deferred until daily refresh interval."
  fi
  PENDING_REFRESH=1
  return 1
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


post_health() {
  local payload http_code curl_exit
  payload='{"type":"health","heartbeat":true}'

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
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] heartbeat sent"
    return 0
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [warn] heartbeat failed curl_exit=$curl_exit http_code=${http_code:-000}" >&2
  return 1
}

max_user_watches="$(read_inotify_limit max_user_watches)"
max_user_instances="$(read_inotify_limit max_user_instances)"
echo "Inotify limits: max_user_watches=$max_user_watches max_user_instances=$max_user_instances"

refresh_watch_targets "startup"
LAST_HEARTBEAT_EPOCH=0

if [[ -p "$EVENT_PIPE" ]]; then
  rm -f "$EVENT_PIPE"
fi
mkfifo "$EVENT_PIPE"
exec 3<>"$EVENT_PIPE"

while true; do
  watch_count="$(wc -l < "$WATCHLIST_FILE" | tr -d '[:space:]')"
  echo "[watch-register] step=inotify-start path=$WATCH_PATH watch_entries=$watch_count events=close_write,moved_to,create"
  inotifywait -m \
    -e close_write -e moved_to -e create \
    --format '%e|%w%f' \
    --fromfile "$WATCHLIST_FILE" > "$EVENT_PIPE" 2>&1 &
  inotify_pid="$!"
  echo "[watch-register] step=inotify-started pid=$inotify_pid pipe=$EVENT_PIPE"
  refresh_now=0

  while true; do
    if should_refresh_now; then
      echo "[info] refreshing watch target list due to daily refresh interval."
      refresh_now=1
      break
    fi

    now_epoch="$(date +%s)"
    if (( HEARTBEAT_INTERVAL_SECONDS > 0 )) && (( now_epoch - LAST_HEARTBEAT_EPOCH >= HEARTBEAT_INTERVAL_SECONDS )); then
      post_health || true
      LAST_HEARTBEAT_EPOCH="$now_epoch"
    fi

    if IFS='|' read -r -t 1 event changed <&3; then
      if [[ "$event" == Setting* || "$event" == Watches* ]]; then
        echo "[watch-register] step=inotify-runtime message=${event}${changed:+|$changed}"
        continue
      fi

      if [[ -z "${changed:-}" ]]; then
        echo "[watch-register] step=inotify-raw message=$event"
        continue
      fi

      if is_excluded_path "$changed" "$EXCLUDED_DIR_NAMES"; then
        continue
      fi

      echo "[$(date '+%Y-%m-%d %H:%M:%S')] event=$event path=$changed"

      if [[ "$event" == *"ISDIR"* ]]; then
        if mark_refresh_if_needed "$changed"; then
          refresh_now=1
          break
        fi
      fi

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
      continue
    fi

    if ! kill -0 "$inotify_pid" 2>/dev/null; then
      echo "[warn] inotifywait exited unexpectedly. restarting watcher."
      refresh_now=1
      break
    fi
  done

  kill "$inotify_pid" 2>/dev/null || true
  wait "$inotify_pid" 2>/dev/null || true

  if [[ $refresh_now -eq 1 ]]; then
    refresh_watch_targets "periodic"
  fi

done
