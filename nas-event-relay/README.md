# NAS Event Relay

`watch-and-notify.sh`는 `inotifywait`로 `WATCH_PATH`를 재귀 감시하고, 파일 변경 이벤트를 GShare 서버로 전달합니다.

## 로그 해석

- 기존에는 `CLOSE_WRITE`일 때만 로그가 찍혀서, `CREATE`/`MOVED_TO` 위주 워크로드에서는 "아무 로그가 없는 것처럼" 보일 수 있었습니다.
- 현재는 감지된 이벤트마다 `event=<...> path=<...>` 로그를 출력하고, 웹훅 전송 성공/실패도 별도 로그로 남깁니다.

- 시작 로그에 `watch_dirs_total`(전체 디렉토리 수)와 `watch_dirs_effective`(제외 패턴 적용 후 이벤트 전송 대상 디렉토리 수)를 함께 출력합니다.
- 전송은 최대 3회(짧은 간격) 재시도하며, 실패 시 `curl_exit`/`http_code`를 함께 로그로 남깁니다.
  - 예: `curl_exit=7`은 대상 서버 연결 실패(서버 미기동/네트워크 경로 문제)
  - 예: `http_code=500`은 GShare 앱 내부 처리 실패

## inotify + Docker 마운트 동작 참고

- **로컬 Linux 파일시스템(ext4/xfs/btrfs) bind mount**는 일반적으로 inotify 이벤트가 정상 전달됩니다.
- **NFS/CIFS/SMB/FUSE 계열 파일시스템**은 이벤트 전달이 불완전할 수 있습니다.
  - 특히 "다른 노드/클라이언트에서 발생한 변경"은 커널 이벤트로 올라오지 않아 누락될 수 있습니다.
- 컨테이너 시작 시 로그에 `fs: <type>`과 경고가 출력되면 이 케이스를 의심해야 합니다.

## 빠른 점검 방법

컨테이너 안에서 직접 테스트하면 inotify 경로 문제를 빠르게 구분할 수 있습니다.

```bash
# 1) 감시 테스트
docker exec -it <relay-container> sh -lc 'inotifywait -m -r -e create -e close_write /watch'

# 2) 다른 터미널에서 컨테이너 내부 파일 생성
docker exec -it <relay-container> sh -lc 'mkdir -p /watch/_probe && echo test > /watch/_probe/a.txt'
```

- 위 테스트에서 이벤트가 나오면 relay 스크립트/필터 설정을 점검합니다.
- 위 테스트에서도 이벤트가 안 나오면 마운트된 파일시스템 특성일 가능성이 큽니다.

## 운영 팁

- 파일 생성 주체를 가능하면 **동일 호스트(동일 커널 컨텍스트)**로 맞춥니다.
- 이벤트 신뢰성이 중요하면 inotify 단독 대신 **주기 스캔(polling) 보조 방식**을 추가하는 것이 안전합니다.

## 제외 디렉토리 기본값

- `EXCLUDED_DIR_NAMES` 기본값은 `@eaDir,@*,.*` 입니다.
- 즉 Synology 메타데이터 폴더(`@eaDir`)뿐 아니라, 이름이 `@`로 시작하는 디렉토리(`@*`)와 숨김 디렉토리(`.*`) 하위 이벤트도 기본적으로 무시합니다.
- 필요하면 환경변수로 쉼표 구분 목록(와일드카드 허용)을 지정해 동작을 바꿀 수 있습니다.
