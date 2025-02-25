# GShare Manager

GShare Manager는 Proxmox 환경에서 Android VM을 효율적으로 관리하기 위한 자동화 도구입니다. NAS 등의 공유 폴더를 모니터링하다가 파일 변경이 감지되면 VM을 자동으로 시작하고, VM의 사용량이 감소하면 자동으로 종료하는 기능을 제공합니다.

## 주요 기능

- 공유 폴더 수정시간간 모니터링
- 폴더 내용 변경 시 자동 VM 시작
- VM CPU 사용량 모니터링
- 저사용량 감지 시 자동 종료 웹훅 전송
- 상태 모니터링 웹 인터페이스

## 사전 준비사항

1. Proxmox에 설치된 Android VM
2. Android VM에 설치된 Macrodroid
   - `/shutdown` 엔드포인트로 웹훅 수신 시 VM이 종료되도록 설정
3. 모니터링할 NAS 폴더
4. Python 3.8 이상

## 설치 방법

1. 저장소 클론
   ```bash
   git clone https://github.com/yourusername/gshare-manager.git
   cd gshare-manager
   ```

2. NAS 폴더 마운트
   ```bash
   # NFS 마운트
   sudo mount -t nfs 192.168.0.100:/volume1/photos /mnt/gshare
   ```

3. 설정 파일 구성
   ```bash
   cp .env.example .env
   # .env 파일을 편집하여 필요한 설정 입력

   cp config.example.py config.py
   # config.py 파일을 편집하여 필요한 설정 입력
   ```

4. 서비스 등록 (root 권한 필요)
   ```bash
   # 서비스 파일 생성
   sudo nano /etc/systemd/system/gshare_manager.service
   ```
   
   gshare_manager.service 내용:
   ```ini
   [Unit]
   Description=GShare Manager Service
   After=network.target

   [Service]
   Type=simple
   User=root
   WorkingDirectory=/설치경로/gshare-manager
   ExecStart=/usr/bin/python3 /설치경로/gshare-manager/gshare_manager.py
   Restart=always
   RestartSec=3

   [Install]
   WantedBy=multi-user.target
   ```

   서비스 등록 및 시작:
   ```bash
   # 서비스 파일 권한 설정
   sudo chmod 644 /etc/systemd/system/gshare_manager.service

   # systemd 데몬 리로드
   sudo systemctl daemon-reload

   # 서비스 등록 및 시작
   sudo systemctl enable gshare_manager
   sudo systemctl start gshare_manager

   # 서비스 상태 확인
   sudo systemctl status gshare_manager
   ```

## 설정 파일

### config.py 주요 설정
- `NODE_NAME`: Proxmox 노드 이름
- `VM_ID`: Android VM의 ID
- `MOUNT_PATH`: 마운트된 공유 폴더 경로 (기본값: /mnt/gshare)
- `CPU_THRESHOLD`: VM 종료 판단을 위한 CPU 사용률 임계값
- `THRESHOLD_COUNT`: 종료 판단을 위한 연속 저사용량 감지 횟수

### .env 파일 설정
- `PROXMOX_HOST`: Proxmox API 주소
- `TOKEN_ID`: Proxmox API 토큰 ID
- `SECRET`: Proxmox API 시크릿
- `SHUTDOWN_WEBHOOK_URL`: VM 종료 웹훅 URL

## 모니터링

웹 인터페이스를 통해 현재 상태를 확인할 수 있습니다:
- http://localhost:5000


## 라이선스

MIT License