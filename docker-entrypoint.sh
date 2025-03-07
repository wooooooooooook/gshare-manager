#!/bin/bash
set -e

echo "GShare 도커 컨테이너 초기화 중..."

# 필요한 디렉토리 생성
mkdir -p /mnt/gshare /mnt/gshare_links /config /logs

# 설정 파일 확인
CONFIG_FILE="/config/config.yaml"
TEMPLATE_FILE="/config/config.yaml.template"

# 설정 파일이 없으면 랜딩 페이지 모드로 실행
if [ ! -f "$CONFIG_FILE" ]; then
    echo "설정 파일이 존재하지 않습니다. 랜딩 페이지로 진입합니다."
    
    # 템플릿 파일이 존재하는지 확인하고 복사
    if [ -f "$TEMPLATE_FILE" ]; then
        echo "템플릿 설정 파일을 발견했습니다."
    else
        echo "템플릿 파일이 없습니다. 기본 템플릿을 생성합니다."
        cat > "$TEMPLATE_FILE" << EOL
# Proxmox 설정
proxmox:
  node_name: ""  # Proxmox 노드 이름 (예: pve)
  vm_id: ""  # Android VM ID (예: 100)
  cpu:
    threshold: 10.0  # CPU 사용량 임계치 (%)
    check_interval: 60  # CPU 사용량 체크 간격 (초)
    threshold_count: 3  # 임계치 체크 횟수

# 마운트 설정
mount:
  path: "/mnt/gshare"  # 로컬 마운트 경로
  folder_size_timeout: 30  # 폴더 용량 확인 시간 초과 (초)

# NFS 설정
nfs:
  path: ""  # NFS 서버 경로 (예: 192.168.1.100:/volume1/shared)

# SMB 설정
smb:
  share_name: "gshare"  # SMB 공유 이름
  comment: "GShare SMB 공유"  # SMB 설명
  guest_ok: false  # 게스트 접근 허용 여부
  read_only: true  # 읽기 전용 여부
  links_dir: "/mnt/gshare_links"  # SMB 링크 디렉토리

# 자격 증명 정보 (보안을 위해 수정 필요)
credentials:
  proxmox_host: ""  # Proxmox 호스트 주소 (예: https://proxmox.example.com:8006)
  token_id: ""  # Proxmox API 토큰 ID (예: user@pam!token)
  secret: ""  # Proxmox API 토큰 시크릿
  shutdown_webhook_url: ""  # MacroDroid 종료 웹훅 URL
  smb_username: ""  # SMB 사용자 이름
  smb_password: ""  # SMB 비밀번호

# 시스템 설정
timezone: "Asia/Seoul"  # 시간대
EOL
    fi
else
    echo "설정 파일이 존재합니다."
    
    # 설정 파일에서 NFS 마운트 정보 추출
    if [ -n "$(grep -A 2 'nfs:' $CONFIG_FILE)" ] && [ -n "$(grep 'path:' $CONFIG_FILE)" ]; then
        NFS_PATH=$(grep -A 1 'nfs:' $CONFIG_FILE | grep 'path:' | gawk '{print $2}' | tr -d '"')
        MOUNT_PATH=$(grep -A 2 'mount:' $CONFIG_FILE | grep 'path:' | gawk '{print $2}' | tr -d '"')
        
        if [ -n "$NFS_PATH" ] && [ -n "$MOUNT_PATH" ]; then
            echo "NFS 마운트 시도: $NFS_PATH -> $MOUNT_PATH"
            mkdir -p "$MOUNT_PATH"
            mount -t nfs "$NFS_PATH" "$MOUNT_PATH" || echo "NFS 마운트 실패: $NFS_PATH -> $MOUNT_PATH"
        fi
    fi
fi

# 작업 디렉토리로 이동
cd /app

# 서비스 시작
echo "GShare 애플리케이션 시작 중..."
exec "$@" 