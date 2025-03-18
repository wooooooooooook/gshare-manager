#!/usr/bin/env bash

# Copyright (c) 2021-2025 community-scripts ORG
# Author: wooooooooooook
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Modified for Gshare installation

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

msg_info "Installing basic dependency packages"
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

msg_ok "Basic dependency packages installation completed"

msg_info "Installing Docker & Compose"
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
msg_ok "Docker & Compose installation completed"

msg_info "Cloning Gshare project"
cd /opt
git clone -b docker https://github.com/wooooooooooook/gshare-manager.git
cd gshare-manager
msg_ok "Gshare project cloning completed"

# Timezone settings
attempts=0
while true; do
    read -r -p "Enter your timezone (default: Asia/Seoul): " TZ_INPUT
    if [ -z "$TZ_INPUT" ]; then
        TZ_INPUT="Asia/Seoul"
        break
    elif validate_tz "$TZ_INPUT"; then
        break
    fi
    msg_error "Invalid timezone! Please enter a valid TZ identifier."

    attempts=$((attempts + 1))
    if [[ "$attempts" -ge 3 ]]; then
        msg_error "Maximum attempts reached. Setting default value (Asia/Seoul)."
        TZ_INPUT="Asia/Seoul"
        break
    fi
done

# Port settings
read -r -p "Enter Flask port (default: 5000): " FLASK_PORT_INPUT
FLASK_PORT_INPUT=${FLASK_PORT_INPUT:-5000}

read -r -p "Enter SMB port (default: 445): " SMB_PORT_INPUT
SMB_PORT_INPUT=${SMB_PORT_INPUT:-445}

# Log level settings
PS3="Select log level: "
options=("DEBUG" "INFO" "WARNING" "ERROR" "CRITICAL")
select LOG_LEVEL_INPUT in "${options[@]}"; do
    if [ -n "$LOG_LEVEL_INPUT" ]; then
        break
    fi
    echo "Please select a valid option."
done

# Update Docker Compose configuration file environment variables
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

msg_info "Building and starting Gshare (please be patient)"
$STD docker compose up -d
CONTAINER_ID=""
for i in {1..60}; do
    CONTAINER_ID=$(docker ps --filter "name=gshare_manager" --format "{{.ID}}")
    if [[ -n "$CONTAINER_ID" ]]; then
        STATUS=$(docker inspect --format '{{.State.Status}}' "$CONTAINER_ID" 2>/dev/null || echo "starting")
        if [[ "$STATUS" == "running" ]]; then
            msg_ok "Gshare is running"
            break
        elif [[ "$STATUS" == "exited" ]]; then
            msg_error "Gshare container has exited! Check the logs."
            docker logs "$CONTAINER_ID"
            exit 1
        fi
    fi
    sleep 2
    [[ $i -eq 60 ]] && msg_error "Gshare container did not start within 120 seconds." && docker logs "$CONTAINER_ID" && exit 1
done
msg_ok "Gshare build and start completed"

motd_ssh
customize
