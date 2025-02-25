import logging
import time
from datetime import datetime
import pytz
from config import Config
from proxmox_api import ProxmoxAPI
from folder_monitor import FolderMonitor
from models import State

# 전역 변수로 GShareManager 인스턴스 선언
gshare_manager = None

class GShareManager:
    def __init__(self, config: Config, proxmox_api: ProxmoxAPI):
        self.config = config
        self.proxmox_api = proxmox_api
        self.low_cpu_count = 0
        self.last_action = "프로그램 시작"
        self.folder_monitor = FolderMonitor(config, proxmox_api)
        self.last_shutdown_time = datetime.fromtimestamp(self.folder_monitor.last_shutdown_time).strftime('%Y-%m-%d %H:%M:%S')
        self._update_state()

    def _update_state(self) -> None:
        """현재 상태를 업데이트합니다."""
        try:
            from web_server import current_state
            
            current_time = datetime.now(pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
            vm_running = self.proxmox_api.is_vm_running()
            cpu_usage = self.proxmox_api.get_cpu_usage() or 0.0
            uptime = self.proxmox_api.get_vm_uptime()
            
            from utils import format_uptime
            uptime_str = format_uptime(uptime)
            
            # 타임존을 고려하여 timestamp를 문자열로 변환
            self.last_shutdown_time = datetime.fromtimestamp(
                self.folder_monitor.last_shutdown_time,
                pytz.timezone(self.config.TIMEZONE)
            ).strftime('%Y-%m-%d %H:%M:%S')

            current_state.last_check_time = current_time
            current_state.vm_running = vm_running
            current_state.cpu_usage = round(cpu_usage, 2)
            current_state.last_action = self.last_action
            current_state.low_cpu_count = self.low_cpu_count
            current_state.threshold_count = self.config.THRESHOLD_COUNT
            current_state.uptime = uptime_str
            current_state.last_shutdown_time = self.last_shutdown_time
            current_state.monitored_folders = self.folder_monitor.get_monitored_folders()
            current_state.smb_running = self.folder_monitor.check_smb_status()
            current_state.check_interval = self.config.CHECK_INTERVAL
            
        except Exception as e:
            logging.error(f"상태 업데이트 실패: {e}")

    def monitor(self) -> None:
        """VM과 폴더 변경 사항을 모니터링합니다."""
        last_vm_status = None  # VM 상태 변화 감지를 위한 변수
        
        while True:
            try:
                from utils import update_log_level
                update_log_level()
                logging.debug("모니터링 루프 시작")
                
                # VM 상태 확인
                current_vm_status = self.proxmox_api.is_vm_running()
                
                # VM 상태가 변경되었고, 현재 종료 상태인 경우
                if last_vm_status is not None and last_vm_status != current_vm_status and not current_vm_status:
                    logging.info("VM이 종료되어 SMB 공유를 비활성화합니다.")
                    self.folder_monitor.smb_manager.deactivate_smb_share()
                
                last_vm_status = current_vm_status
                
                try:
                    logging.debug("폴더 수정 시간 변화 확인 중")
                    changed_folders, should_start_vm = self.folder_monitor.check_modifications()
                    if changed_folders:
                        # 변경된 폴더들의 SMB 공유 활성화
                        if self.folder_monitor.smb_manager.activate_smb_share(
                            self.folder_monitor.nfs_uid, self.folder_monitor.nfs_gid
                        ):
                            self.last_action = f"SMB 공유 활성화: {', '.join(changed_folders)}"
                        
                        # VM이 정지 상태이고 최근 수정된 파일이 있는 경우에만 시작
                        if not self.proxmox_api.is_vm_running() and should_start_vm:
                            self.last_action = "VM 시작"
                            if self.proxmox_api.start_vm():
                                logging.info("VM 시작 성공")
                            else:
                                logging.error("VM 시작 실패")
                except Exception as e:
                    logging.error(f"파일시스템 모니터링 중 오류: {e}")

                try:
                    if self.proxmox_api.is_vm_running():
                        cpu_usage = self.proxmox_api.get_cpu_usage()
                        if cpu_usage is not None:
                            logging.debug(f"현재 CPU 사용량: {cpu_usage}%")
                            if cpu_usage < self.config.CPU_THRESHOLD:
                                self.low_cpu_count += 1
                                logging.debug(f"낮은 CPU 사용량 카운트: {self.low_cpu_count}/{self.config.THRESHOLD_COUNT}")
                                if self.low_cpu_count >= self.config.THRESHOLD_COUNT:
                                    self.last_action = "종료 웹훅 전송"
                                    self.folder_monitor.send_shutdown_webhook()
                                    self.low_cpu_count = 0
                            else:
                                self.low_cpu_count = 0
                except Exception as e:
                    logging.error(f"VM 모니터링 중 오류: {e}")

                try:
                    self._update_state()
                except Exception as e:
                    logging.error(f"상태 업데이트 중 오류: {e}")

                time.sleep(self.config.CHECK_INTERVAL)
                
            except Exception as e:
                logging.error(f"모니터링 루프에서 예상치 못한 오류 발생: {e}")
                time.sleep(self.config.CHECK_INTERVAL)  # 오류 발생시에도 대기 후 계속 실행
