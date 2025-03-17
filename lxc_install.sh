#!/usr/bin/env bash

# Copyright (c) 2021-2025 community-scripts ORG
# Author: MickLesk (CanbiZ)
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Modified for Gshare installation

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

msg_info "기본 의존성 패키지 설치 중"
$STD apk add \
    newt \
    curl \
    openssh \
    tzdata \
    nano \
    gawk \
    yq \
    mc \
    git

msg_ok "기본 의존성 패키지 설치 완료"

msg_info "Docker & Compose 설치 중"
$STD apk add docker
$STD rc-service docker start
$STD rc-update add docker default

get_latest_release() {
    curl -sL https://api.github.com/repos/$1/releases/latest | grep '"tag_name":' | cut -d'"' -f4
}
DOCKER_COMPOSE_LATEST_VERSION=$(get_latest_release "docker/compose")
DOCKER_CONFIG=${DOCKER_CONFIG:-$HOME/.docker}
mkdir -p $DOCKER_CONFIG/cli-plugins
curl -sSL https://github.com/docker/compose/releases/download/$DOCKER_COMPOSE_LATEST_VERSION/docker-compose-linux-x86_64 -o ~/.docker/cli-plugins/docker-compose
chmod +x $DOCKER_CONFIG/cli-plugins/docker-compose
msg_ok "Docker & Compose 설치 완료"

msg_info "Gshare 프로젝트 클론"
cd /opt
git clone -b docker https://github.com/wooooooooooook/gshare-manager.git
cd gshare-manager
msg_ok "Gshare 프로젝트 클론 완료"

# 타임존 설정
attempts=0
while true; do
    read -r -p "타임존을 입력하세요 (기본값: Asia/Seoul): " TZ_INPUT
    if [ -z "$TZ_INPUT" ]; then
        TZ_INPUT="Asia/Seoul"
        break
    elif validate_tz "$TZ_INPUT"; then
        break
    fi
    msg_error "잘못된 타임존입니다! 유효한 TZ 식별자를 입력하세요."

    attempts=$((attempts + 1))
    if [[ "$attempts" -ge 3 ]]; then
        msg_error "최대 시도 횟수에 도달했습니다. 기본값(Asia/Seoul)으로 설정합니다."
        TZ_INPUT="Asia/Seoul"
        break
    fi
done

# 포트 설정
read -r -p "Flask 포트를 입력하세요 (기본값: 5000): " FLASK_PORT_INPUT
FLASK_PORT_INPUT=${FLASK_PORT_INPUT:-5000}

read -r -p "SMB 포트를 입력하세요 (기본값: 445): " SMB_PORT_INPUT
SMB_PORT_INPUT=${SMB_PORT_INPUT:-445}

# 로그 레벨 설정
PS3="로그 레벨을 선택하세요: "
options=("DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL")
select LOG_LEVEL_INPUT in "${options[@]}"; do
    if [ -n "$LOG_LEVEL_INPUT" ]; then
        break
    fi
    echo "유효한 옵션을 선택하세요."
done

# Docker Compose 설정 파일 환경 변수 업데이트
yq -i "
  .services.gshare.environment = [
    \"TZ=$TZ_INPUT\",
    \"FLASK_PORT=$FLASK_PORT_INPUT\",
    \"SMB_PORT=445\",
    \"LOG_LEVEL=$LOG_LEVEL_INPUT\"
  ] |
  .services.gshare.ports = [
    \"${FLASK_PORT_INPUT}:${FLASK_PORT_INPUT}\",
    \"${SMB_PORT_INPUT}:445\"
  ]
" docker-compose.yml

msg_info "Gshare 빌드 및 시작 중 (인내심을 가지고 기다려주세요)"
$STD docker compose up -d
CONTAINER_ID=""
for i in {1..60}; do
    CONTAINER_ID=$(docker ps --filter "name=gshare_manager" --format "{{.ID}}")
    if [[ -n "$CONTAINER_ID" ]]; then
        STATUS=$(docker inspect --format '{{.State.Status}}' "$CONTAINER_ID" 2>/dev/null || echo "starting")
        if [[ "$STATUS" == "running" ]]; then
            msg_ok "Gshare가 실행 중입니다"
            break
        elif [[ "$STATUS" == "exited" ]]; then
            msg_error "Gshare 컨테이너가 종료되었습니다! 로그를 확인하세요."
            docker logs "$CONTAINER_ID"
            exit 1
        fi
    fi
    sleep 2
    [[ $i -eq 60 ]] && msg_error "Gshare 컨테이너가 120초 내에 실행되지 않았습니다." && docker logs "$CONTAINER_ID" && exit 1
done
msg_ok "Gshare 빌드 및 시작 완료"

# 설정 정보 저장
mkdir -p /opt/gshare/info
echo -e "설치 정보:\n-----------------\n" > /opt/gshare/info/setup_info.txt
echo -e "타임존: $TZ_INPUT" >> /opt/gshare/info/setup_info.txt
echo -e "Flask 포트: $FLASK_PORT_INPUT" >> /opt/gshare/info/setup_info.txt
echo -e "SMB 포트: $SMB_PORT_INPUT" >> /opt/gshare/info/setup_info.txt
echo -e "로그 레벨: $LOG_LEVEL_INPUT" >> /opt/gshare/info/setup_info.txt
msg_ok "설정 정보를 /opt/gshare/info/setup_info.txt에 저장했습니다"

motd_ssh
customize

msg_info "설치 완료"
IP=$(hostname -I | awk '{print $1}')
echo -e "\n\nGshare가 설치되었습니다!\n"
echo -e "웹 인터페이스 접속: http://$IP:$FLASK_PORT_INPUT\n" 
echo -e "SMB 접속: \\\\$IP:$SMB_PORT_INPUT\n"
echo -e "설정 정보는 /opt/gshare/info/setup_info.txt 파일에 저장되어 있습니다.\n"