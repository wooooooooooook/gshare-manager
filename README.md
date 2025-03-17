# GShare Manager

GShare Manager는 Proxmox 환경에서 Android VM을 효율적으로 관리하기 위한 자동화 도구입니다. NAS 등의 공유 폴더를 모니터링하다가 파일 변경이 감지되면 VM을 자동으로 시작하고, VM의 사용량이 감소하면 자동으로 종료하는 기능을 제공합니다.

## 주요 기능

- NAS로부터 NFS로 폴더를 공유받아 개별 폴더의 수정 시간 모니터링
- 최근 수정 감지 시 자동 Android VM 시작 및 해당 폴더만 SMB 공유 시작
- VM CPU 사용량 모니터링하여 Idle상태 시 자동 종료 웹훅 전송 및 SMB 공유 중단
- 상태 모니터링 웹 인터페이스

## 사전 준비사항

1. Proxmox에 설치된 Android VM
2. Android VM에 설치된 Macrodroid
   - `/shutdown` 엔드포인트로 웹훅 수신 시 VM이 종료되도록 설정
3. 모니터링할 NAS 폴더

## 설치 방법

- SMB포트(445) 사용이 가능한 도커환경
- (docker-compose.yml)[docker-compose.yml]을 통한 설치

## 모니터링

웹 인터페이스를 통해 현재 상태를 확인할 수 있습니다:
- http://localhost:5000


## 라이선스

MIT License