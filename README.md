# GShare Manager

GShare Manager는 Proxmox 환경에서 Android VM을 효율적으로 관리하기 위한 자동화 도구입니다. 기본 동작은 NAS에서 전달되는 inotify 이벤트를 수신해 폴더를 SMB 공유하고 VM을 자동으로 시작하며, VM의 사용량이 감소하면 자동으로 종료합니다.

## 주요 기능

- NAS로부터 NFS로 폴더를 공유받아 파일이 있는 폴더만 모니터링하되, 동일 경로에서 중간/하위 폴더 모두 변경되면 상위 폴더만 대상으로 처리
- 최근 수정 감지 시 자동 Android VM 시작 및 해당 폴더만 SMB 공유 시작
- VM CPU 사용량 모니터링하여 Idle상태 시 자동 종료 웹훅 전송 및 SMB 공유 중단
- 상태 모니터링 웹 인터페이스

## 사전 준비사항

- Proxmox VE 8.1 이상(9.x 포함)
- Proxmox에 설치된 Android VM (구글포토, Macrodroid 설치)
- Proxmox API token, secret 준비, 권한은 androidVM에 대해서만 주면 됩니다. (아래 예시)
- ![예시](https://github.com/user-attachments/assets/b38d3cdc-65c4-4762-bb57-2dd20b6279ca)

- Macrodroid에서 `/shutdown` 엔드포인트로 웹훅 수신 시 VM이 종료되도록 설정. 웹훅주소 (예: http://192.168.1.9:8080/shutdown)
- ![스크린샷 2025-03-18 114622](https://github.com/user-attachments/assets/5ac321a8-090d-48f0-b371-fd025c6d422f)


## 설치 방법
### 자동 설치
- proxmox node shell에 입력
- `bash -c "$(wget -qLO - https://raw.githubusercontent.com/wooooooooooook/gshare-manager/refs/heads/main/lxc_update.sh)"`
- proxmox community script로 만들었습니다. apline linux CT에 docker환경으로 설치됩니다.

### 수동 설치
- SMB포트(445) 사용이 가능한 도커환경
- 본 저장소를 clone후 `git clone -b docker https://github.com/wooooooooooook/gshare-manager.git`
- `cd gshare-manager && docker compse up -d --build`

## 설치 후
1. Android VM에 설치된 Macrodroid에서
   - 부팅후 `su --mount-master -c mount -t cifs //{도커호스트주소}/gshare /mnt/runtime/default/emulated/0/DCIM/1 -o username={SMB유저},password={SMB비번},ro,iocharset=utf8` 스크립트 실행으로 마운트 시키는 자동화
   - ![스크린샷 2025-03-18 114451](https://github.com/user-attachments/assets/4d30918f-ac22-4129-912d-a1b0bd85602b)


2. 모니터링할 NAS 폴더를 도커호스트에 NFS 공유하기
   ![NFS 설정 예시](/docs/img/nfs.png)
4. 안내되는 주소로 (예: 192.168.1.10:5000) 접속하여 초기설정을 완료하면 모니터링이 시작됩니다.


## 업데이트

도커 호스트 쉘에서 root계정으로 `update`실행 또는 설치 스크립트를 다시 실행한 뒤 `Update existing GShare installation`을 선택하면 자동으로 업데이트가 진행됩니다.
직접 업데이트하려면 아래 커맨드를 실행하세요.
```
cd /opt/gshare-manager
git pull
docker compose down
docker compose up -d --build
```

## 라이선스

MIT License


## NAS 이벤트 수신(inotify) 구성

폴링 대신 NAS에서 inotify 이벤트를 직접 전송하려면 `nas-event-relay/`를 NAS에 배포하세요.

1. NAS에서 `nas-event-relay/docker-compose.yml`의 값을 환경에 맞게 수정
   - `GSHARE_EVENT_URL`: gshare_manager의 `/api/folder-event` 주소
   - `EVENT_AUTH_TOKEN`: GShare 설정의 이벤트 인증 토큰과 동일하게 설정
   - `EXCLUDED_DIR_NAMES`(선택): 이벤트 제외 디렉토리명(쉼표 구분), 기본값 `@eaDir`
   - 볼륨: 원본 파일이 생성되는 NAS 경로를 `/watch`로 마운트
2. NAS에서 `docker compose up -d --build` 실행
3. GShare 설정 페이지의 NFS 설정 탭에서
   - NFS 폴더 감시 방식: `이벤트 수신 (event)`
   - 이벤트 인증 토큰: relay와 동일 값

이후 새 파일 생성/이동 이벤트가 발생하면 해당 폴더명이 GShare로 전달되고 SMB 공유 및 VM 시작이 순차 수행됩니다.
파일 쓰기 완료(`close_write`)로 수정시간(mtime) 변화가 감지되면 relay 컨테이너 로그에 감지 경로를 남깁니다.

> relay는 재귀 감시(`inotifywait -r`)를 사용하며, Synology DSM 메타데이터 디렉토리(기본: `@eaDir`)는 이벤트 전송에서 제외합니다.
> 추가 제외 디렉토리가 필요하면 `EXCLUDED_DIR_NAMES` 환경변수에 쉼표로 구분해 지정하세요. 예: `@eaDir,#recycle`
