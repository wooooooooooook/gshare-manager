#!/usr/bin/env bash
source <(curl -s https://raw.githubusercontent.com/wooooooooooook/gshare-manager/refs/heads/docker/build.func)
# Copyright (c) 2021-2025 community-scripts ORG
# License: MIT | https://github.com/community-scripts/ProxmoxVED/raw/main/LICENSE

APP="Gshare"
var_tags="gshare"
var_cpu="1"
var_ram="512"
var_disk="2"
var_os="alpine"
var_version="3.21"
var_unprivileged="0"

header_info "$APP"
variables
color
catch_errors

function update_script() {
    UPD=$(whiptail --backtitle "Proxmox VE Helper Scripts" --title "SUPPORT" --radiolist --cancel-button Exit-Script "Spacebar = Select" 11 58 1 \
        "1" "Check for Alpine Updates" ON \
        3>&1 1>&2 2>&3)

    header_info
    if [ "$UPD" == "1" ]; then
        cd /opt/gshare-manager
        git pull
        docker compose down
        docker compose up -d --build
        exit
    fi
}

start
build_container
description

msg_ok "Completed Successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW} Access it using the following URL:${CL}"
echo -e "${TAB}${GATEWAY}${BGN}https://${IP}:5000${CL}"