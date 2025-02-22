import logging
from logging.handlers import RotatingFileHandler
import time
from dataclasses import dataclass, asdict
from typing import Optional
import requests
import subprocess
from datetime import datetime
import pytz
import json
from config import Config  # 새로운 import 추가
from dotenv import load_dotenv, set_key
import os
from flask import Flask, jsonify, render_template
import threading
import sys

# Flask 로깅 비활성화
cli = sys.modules['flask.cli']
cli.show_server_banner = lambda *x: None

app = Flask(__name__)
# Flask 기본 로거 비활성화
app.logger.disabled = True
log = logging.getLogger('werkzeug')
log.disabled = True

# 전역 변수로 상태와 관리자 객체 선언
current_state = None
gshare_manager = None

@dataclass
class State:
    last_check_time: str
    vm_status: str  # 🔴 (정지), 🟢 (실행 중)
    cpu_usage: float
    last_modified_folder: str  # 가장 최근에 수정된 폴더 이름
    last_modified_time: str    # 해당 폴더의 수정 시간
    last_action: str
    low_cpu_count: int
    uptime: str
    last_shutdown_time: str
    monitored_folders: dict    # 감시 중인 폴더들과 수정 시간
    
    def to_dict(self):
        return asdict(self)

class ProxmoxAPI:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.verify = False
        self._set_token_auth()

    def _set_token_auth(self) -> None:
        # API 토큰을 사용하여 인증 헤더 설정
        self.session.headers.update({
            "Authorization": f"PVEAPIToken={self.config.TOKEN_ID}={self.config.SECRET}"
        })
        logging.info("Proxmox API 토큰 인증 설정 완료")
        self.session.timeout = (5, 10)  # (connect timeout, read timeout)

    def is_vm_running(self) -> bool:
        try:
            response = self.session.get(
                f"{self.config.PROXMOX_HOST}/nodes/{self.config.NODE_NAME}/qemu/{self.config.VM_ID}/status/current"
            )
            response.raise_for_status()
            result = response.json()["data"]["status"]
            logging.debug(f"VM 상태 확인 응답: {result}")
            return result == "running"
        except Exception as e:
            logging.error(f"VM 상태 확인 실패: {e}")
            return False    
    
    def get_vm_uptime(self) -> Optional[float]:
        try:
            response = self.session.get(
                f"{self.config.PROXMOX_HOST}/nodes/{self.config.NODE_NAME}/qemu/{self.config.VM_ID}/status/current"
            )
            response.raise_for_status()
            result = response.json()["data"]["uptime"]
            logging.debug(f"VM 부팅 시간 확인 응답: {result}")
            return result
        except Exception as e:
            logging.error(f"VM 부팅 시간 확인 실패: {e}")
            return None

    def get_cpu_usage(self) -> Optional[float]:
        try:
            response = self.session.get(
                f"{self.config.PROXMOX_HOST}/nodes/{self.config.NODE_NAME}/qemu/{self.config.VM_ID}/status/current"
            )
            response.raise_for_status()
            result = response.json()["data"]["cpu"] * 100
            logging.debug(f"CPU 사용량 확인 응답: {result}")
            return result
        except Exception as e:
            logging.error(f"CPU 사용량 확인 실패: {e}")
            return None

    def start_vm(self) -> bool:
        try:
            response = self.session.post(
                f"{self.config.PROXMOX_HOST}/nodes/{self.config.NODE_NAME}/qemu/{self.config.VM_ID}/status/start"
            )
            response.raise_for_status()
            logging.debug(f"VM 시작 응답 받음")
            return True
        except Exception as e:
            return False

class FolderMonitor:
    def __init__(self, config: Config):
        self.config = config
        self.previous_mtimes = {}  # 각 서브폴더별 이전 수정 시간을 저장
        self.active_shares = set()  # 현재 활성화된 SMB 공유 목록
        self.last_modified_folder = "-"
        self.last_modified_time = "-"
        self.last_vm_start_time = self._load_last_vm_start_time()  # VM 마지막 시작 시간
        self._update_subfolder_mtimes()
        self._ensure_smb_installed()
        self._init_smb_config()

    def _load_last_vm_start_time(self) -> float:
        """VM 마지막 시작 시간을 로드"""
        try:
            if os.path.exists('last_vm_start.txt'):
                with open('last_vm_start.txt', 'r') as f:
                    return float(f.read().strip())
        except Exception as e:
            logging.error(f"VM 마지막 시작 시간 로드 실패: {e}")
        return 0

    def _save_last_vm_start_time(self) -> None:
        """현재 시간을 VM 마지막 시작 시간으로 저장"""
        try:
            current_time = time.time()
            with open('last_vm_start.txt', 'w') as f:
                f.write(str(current_time))
            self.last_vm_start_time = current_time
            logging.info(f"VM 시작 시간 저장됨: {datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            logging.error(f"VM 시작 시간 저장 실패: {e}")

    def _ensure_smb_installed(self):
        """Samba가 설치되어 있는지 확인하고 설치"""
        try:
            subprocess.run(['which', 'smbd'], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            logging.info("Samba가 설치되어 있지 않습니다. 설치를 시도합니다.")
            try:
                subprocess.run(['sudo', 'apt-get', 'update'], check=True)
                subprocess.run(['sudo', 'apt-get', 'install', '-y', 'samba'], check=True)
                logging.info("Samba 설치 완료")
            except subprocess.CalledProcessError as e:
                logging.error(f"Samba 설치 실패: {e}")
                raise

    def _init_smb_config(self):
        """기본 SMB 설정 초기화"""
        try:
            # smb.conf 파일 백업
            if not os.path.exists('/etc/samba/smb.conf.backup'):
                subprocess.run(['sudo', 'cp', '/etc/samba/smb.conf', '/etc/samba/smb.conf.backup'], check=True)

            # 기본 설정 생성
            base_config = """[global]
   workgroup = WORKGROUP
   server string = Samba Server
   server role = standalone server
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no
   # SMB1 설정
   server min protocol = NT1
   server max protocol = NT1
"""
            # 기본 설정 저장
            with open('/etc/samba/smb.conf', 'w') as f:
                f.write(base_config)

            # Samba 사용자 추가
            try:
                subprocess.run(['sudo', 'smbpasswd', '-a', self.config.SMB_USERNAME],
                             input=f"{self.config.SMB_PASSWORD}\n{self.config.SMB_PASSWORD}\n".encode(),
                             capture_output=True)
            except subprocess.CalledProcessError:
                pass  # 사용자가 이미 존재하는 경우 무시

            logging.info("SMB 기본 설정 초기화 완료")
        except Exception as e:
            logging.error(f"SMB 기본 설정 초기화 실패: {e}")
            raise

    def _get_subfolders(self) -> list[str]:
        """마운트 경로의 모든 서브폴더를 재귀적으로 반환"""
        try:
            if not os.path.exists(self.config.MOUNT_PATH):
                logging.error(f"마운트 경로가 존재하지 않음: {self.config.MOUNT_PATH}")
                return []

            logging.debug(f"마운트 경로 스캔 시작: {self.config.MOUNT_PATH}")
            subfolders = []
            
            try:
                for root, dirs, _ in os.walk(self.config.MOUNT_PATH, followlinks=True):
                    # @로 시작하는 폴더 제외
                    dirs[:] = [d for d in dirs if not d.startswith('@')]
                    
                    for dir_name in dirs:
                        try:
                            full_path = os.path.join(root, dir_name)
                            # 마운트 경로로부터의 상대 경로 계산
                            rel_path = os.path.relpath(full_path, self.config.MOUNT_PATH)
                            
                            # 숨김 폴더 제외
                            if not any(part.startswith('.') for part in rel_path.split(os.sep)):
                                # 폴더 접근 권한 확인
                                if os.access(full_path, os.R_OK):
                                    subfolders.append(rel_path)
                                    logging.debug(f"폴더 감지됨: {rel_path}")
                                else:
                                    logging.warning(f"폴더 접근 권한 없음: {rel_path}")
                        except Exception as e:
                            logging.error(f"개별 폴더 처리 중 오류 발생 ({dir_name}): {e}")
                            continue
            except Exception as e:
                logging.error(f"폴더 순회 중 오류 발생: {e}")
                return []

            logging.debug(f"감지된 전체 폴더 수: {len(subfolders)}")
            return subfolders
            
        except Exception as e:
            logging.error(f"서브폴더 목록 가져오기 실패: {e}")
            return []

    def _get_folder_mtime(self, path: str) -> float:
        """지정된 경로의 폴더 수정 시간을 반환"""
        try:
            full_path = os.path.join(self.config.MOUNT_PATH, path)
            return os.path.getmtime(full_path)
        except Exception as e:
            logging.error(f"폴더 수정 시간 확인 중 오류 발생 ({path}): {e}")
            return self.previous_mtimes.get(path, 0)

    def _update_subfolder_mtimes(self) -> None:
        """모든 서브폴더의 수정 시간을 업데이트"""
        subfolders = self._get_subfolders()
        for subfolder in subfolders:
            if subfolder not in self.previous_mtimes:
                self.previous_mtimes[subfolder] = self._get_folder_mtime(subfolder)

    def check_modifications(self) -> tuple[list[str], bool]:
        """수정 시간이 변경된 서브폴더 목록과 VM 시작 필요 여부를 반환"""
        changed_folders = []
        should_start_vm = False
        self._update_subfolder_mtimes()
        
        for path, prev_mtime in self.previous_mtimes.items():
            current_mtime = self._get_folder_mtime(path)
            if current_mtime != prev_mtime:
                last_modified = datetime.fromtimestamp(current_mtime).strftime('%Y-%m-%d %H:%M:%S')
                logging.info(f"폴더 수정 시간 변화 감지 ({path}): {last_modified}")
                changed_folders.append(path)
                self.previous_mtimes[path] = current_mtime
                self.last_modified_folder = path
                self.last_modified_time = last_modified
                
                # VM 마지막 시작 시간보다 수정 시간이 더 최근인 경우
                if current_mtime > self.last_vm_start_time:
                    should_start_vm = True
                    logging.info(f"VM 시작 조건 충족 - 수정 시간: {last_modified}")
        
        return changed_folders, should_start_vm

    def update_vm_start_time(self) -> None:
        """VM이 시작될 때 호출하여 시작 시간을 업데이트"""
        self._save_last_vm_start_time()

    @property
    def total_size(self) -> int:
        """전체 폴더 크기 반환 (웹 인터페이스 표시용)"""
        total = 0
        for path in self.previous_mtimes.keys():
            try:
                cmd = f"du -sb {path} 2>/dev/null | cut -f1"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=self.config.GET_FOLDER_SIZE_TIMEOUT)
                if result.returncode == 0:
                    total += int(result.stdout.strip())
            except Exception as e:
                logging.error(f"폴더 크기 계산 중 오류 발생 ({path}): {e}")
        return total

    def get_monitored_folders(self) -> dict:
        """감시 중인 모든 폴더와 수정 시간을 반환"""
        monitored_folders = {}
        for path in self.previous_mtimes.keys():
            mtime = self._get_folder_mtime(path)
            monitored_folders[path] = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        return monitored_folders

    def _activate_smb_share(self, subfolder: str) -> bool:
        """특정 서브폴더의 SMB 공유를 활성화"""
        try:
            if subfolder in self.active_shares:
                return True

            source_path = os.path.join(self.config.MOUNT_PATH, subfolder)
            # 폴더 경로의 슬래시를 언더스코어로 변환하여 공유 이름 생성
            share_name = f"{self.config.SMB_SHARE_NAME}_{subfolder.replace(os.sep, '_')}"
            
            # 공유 설정 생성
            share_config = f"""
[{share_name}]
   path = {source_path}
   comment = {self.config.SMB_COMMENT} - {subfolder}
   browseable = yes
   guest ok = {'yes' if self.config.SMB_GUEST_OK else 'no'}
   read only = {'yes' if self.config.SMB_READ_ONLY else 'no'}
   create mask = 0777
   directory mask = 0777
   force user = {self.config.SMB_USERNAME}
"""
            # 설정 추가
            with open('/etc/samba/smb.conf', 'a') as f:
                f.write(share_config)
            
            # Samba 서비스 재시작
            subprocess.run(['sudo', 'systemctl', 'restart', 'smbd'], check=True)
            subprocess.run(['sudo', 'systemctl', 'restart', 'nmbd'], check=True)
            
            self.active_shares.add(subfolder)
            logging.info(f"SMB 공유 활성화 성공: {subfolder}")
            return True
        except Exception as e:
            logging.error(f"SMB 공유 활성화 실패 ({subfolder}): {e}")
            return False

    def _deactivate_smb_share(self, subfolder: str = None) -> bool:
        """SMB 공유를 비활성화. subfolder가 None이면 모든 공유 비활성화"""
        try:
            if subfolder is not None and subfolder not in self.active_shares:
                return True

            # smb.conf 파일 읽기
            with open('/etc/samba/smb.conf', 'r') as f:
                lines = f.readlines()

            # 기본 설정만 유지
            if subfolder is None:
                # [global] 섹션까지만 유지
                new_lines = []
                for line in lines:
                    if line.strip().startswith('[') and not line.strip() == '[global]':
                        break
                    new_lines.append(line)
                lines = new_lines
                self.active_shares.clear()
            else:
                # 특정 공유 설정만 제거
                share_name = f"{self.config.SMB_SHARE_NAME}_{subfolder}"
                new_lines = []
                skip = False
                for line in lines:
                    if line.strip() == f'[{share_name}]':
                        skip = True
                        continue
                    if skip and line.strip().startswith('['):
                        skip = False
                    if not skip:
                        new_lines.append(line)
                lines = new_lines
                self.active_shares.discard(subfolder)

            # 설정 파일 저장
            with open('/etc/samba/smb.conf', 'w') as f:
                f.writelines(lines)

            # Samba 서비스 재시작
            subprocess.run(['sudo', 'systemctl', 'restart', 'smbd'], check=True)
            subprocess.run(['sudo', 'systemctl', 'restart', 'nmbd'], check=True)

            if subfolder is None:
                logging.info("모든 SMB 공유 비활성화 완료")
            else:
                logging.info(f"SMB 공유 비활성화 완료: {subfolder}")
            return True
        except Exception as e:
            logging.error(f"SMB 공유 비활성화 실패: {e}")
            return False

class GShareManager:
    def __init__(self, config: Config, proxmox_api: ProxmoxAPI):
        self.config = config
        self.proxmox_api = proxmox_api
        self.low_cpu_count = 0
        self.last_action = "프로그램 시작"
        self.last_shutdown_time = "-"
        self.folder_monitor = FolderMonitor(config)
        self._update_state()

    def _format_uptime(self, seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours}시간 {minutes}분 {secs}초"
        elif minutes > 0:
            return f"{minutes}분 {secs}초"
        else:
            return f"{secs}초"

    def _send_shutdown_webhook(self) -> None:
        if self.proxmox_api.is_vm_running():
            try:
                response = requests.post(self.config.SHUTDOWN_WEBHOOK_URL, timeout=5)
                response.raise_for_status()

                uptime = self.proxmox_api.get_vm_uptime()
                uptime_str = self._format_uptime(uptime) if uptime is not None else "알 수 없음"
                self.last_shutdown_time = datetime.now(pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
                
                # VM 종료 시 모든 SMB 공유 비활성화
                self.folder_monitor._deactivate_smb_share()
                
                logging.info(f"종료 웹훅 전송 성공, 업타임: {uptime_str}")
            except Exception as e:
                logging.error(f"종료 웹훅 전송 실패: {e}")
        else:
            logging.info("종료 웹훅을 전송하려했지만 vm이 이미 종료상태입니다.")

    def _update_state(self) -> None:
        try:
            global current_state
            current_time = datetime.now(pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
            vm_status = "🟢" if self.proxmox_api.is_vm_running() else "🔴"
            cpu_usage = self.proxmox_api.get_cpu_usage() or 0.0
            uptime = self.proxmox_api.get_vm_uptime()
            uptime_str = self._format_uptime(uptime) if uptime is not None else "알 수 없음"

            current_state = State(
                last_check_time=current_time,
                vm_status=vm_status,
                cpu_usage=round(cpu_usage, 2),
                last_modified_folder=self.folder_monitor.last_modified_folder,
                last_modified_time=self.folder_monitor.last_modified_time,
                last_action=self.last_action,
                low_cpu_count=self.low_cpu_count,
                uptime=uptime_str,
                last_shutdown_time=self.last_shutdown_time,
                monitored_folders=self.folder_monitor.get_monitored_folders()
            )
            logging.debug(f"상태 업데이트: {current_state.to_dict()}")
        except Exception as e:
            logging.error(f"상태 업데이트 실패: {e}")

    def monitor(self) -> None:
        while True:
            try:
                update_log_level()
                logging.debug("모니터링 루프 시작")
                
                try:
                    logging.debug("폴더 수정 시간 변화 확인 중")
                    changed_folders, should_start_vm = self.folder_monitor.check_modifications()
                    if changed_folders:
                        # 변경된 폴더들의 SMB 공유 활성화
                        for folder in changed_folders:
                            if self.folder_monitor._activate_smb_share(folder):
                                self.last_action = f"SMB 공유 활성화: {folder}"
                        
                        # VM이 정지 상태이고 최근 수정된 파일이 있는 경우에만 시작
                        if not self.proxmox_api.is_vm_running() and should_start_vm:
                            self.last_action = "VM 시작"
                            if self.proxmox_api.start_vm():
                                logging.info("VM 시작 성공")
                                self.folder_monitor.update_vm_start_time()
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
                                    self._send_shutdown_webhook()
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

def setup_logging():
    load_dotenv()
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    
    # 타임존 설정을 위한 Formatter 생성
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    formatter.converter = lambda *args: datetime.now(tz=pytz.timezone(Config().TIMEZONE)).timetuple()
    
    # 핸들러 설정
    file_handler = RotatingFileHandler('gshare_manager.log', maxBytes=5*1024*1024, backupCount=1)  # 5MB 크기
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    
    # 로거 설정
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level))
    
    # 기존 핸들러가 있다면 레벨만 변경
    if logger.handlers:
        logger.setLevel(getattr(logging, log_level))
    else:
        # 새로운 핸들러 추가
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
    
    return logger

def update_log_level():
    """로그 레벨을 동적으로 업데이트"""
    load_dotenv()
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    logging.getLogger().setLevel(getattr(logging, log_level))

def format_size(size_in_bytes: int) -> str:
    """바이트 단위의 크기를 사람이 읽기 쉬운 형식으로 변환"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} PB"

def get_time_ago(timestamp_str):
    try:
        seoul_tz = pytz.timezone('Asia/Seoul')
        last_check = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        last_check = seoul_tz.localize(last_check)
        now = datetime.now(seoul_tz)
        
        diff = now - last_check
        seconds = diff.total_seconds()
        
        time_ago = ""
        if seconds < 150:
            time_ago = f"{int(seconds)}초 전"
        elif seconds < 3600:  # 1시간
            time_ago = f"{int(seconds / 60)}분 전"
        elif seconds < 86400:  # 1일
            time_ago = f"{int(seconds / 3600)}시간 전"
        else:
            time_ago = f"{int(seconds / 86400)}일 전"
            
        return time_ago
    except:
        return timestamp_str

@app.route('/')
def show_log():
    log_content = ""
    if os.path.exists('gshare_manager.log'):
        with open('gshare_manager.log', 'r') as file:
            log_content = file.read()
    else:
        return "Log file not found.", 404

    if current_state is None:
        return "State not initialized.", 404

    return render_template('index.html', 
                         state=current_state.to_dict(), 
                         log_content=log_content, 
                         get_time_ago=get_time_ago)

@app.route('/update_state')
def update_state():
    if current_state is None:
        return jsonify({"error": "State not initialized."}), 404
    return jsonify(current_state.to_dict())

@app.route('/update_log')
def update_log():
    if os.path.exists('gshare_manager.log'):
        with open('gshare_manager.log', 'r') as file:
            log_content = file.read()
            return log_content
    else:
        return "Log file not found.", 404

@app.route('/restart_service')
def restart_service():
    try:
        subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager.service'], check=True)
        subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager_log_server.service'], check=True)
        return jsonify({"status": "success", "message": "서비스가 재시작되었습니다."})
    except subprocess.CalledProcessError as e:
        return jsonify({"status": "error", "message": f"서비스 재시작 실패: {str(e)}"}), 500

@app.route('/retry_mount')
def retry_mount():
    try:
        subprocess.run(['sudo', 'mount', config.MOUNT_PATH], check=True)
        subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager.service'], check=True)
        subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager_log_server.service'], check=True)
        return jsonify({"status": "success", "message": "마운트 재시도 및 서비스를 재시작했습니다."})
    except subprocess.CalledProcessError as e:
        return jsonify({"status": "error", "message": f"마운트 재시도 실패: {str(e)}"}), 500

@app.route('/clear_log')
def clear_log():
    try:
        open('gshare_manager.log', 'w').close()
        return jsonify({"status": "success", "message": "로그가 성공적으로 삭제되었습니다."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"로그 삭제 실패: {str(e)}"}), 500

@app.route('/trim_log/<int:lines>')
def trim_log(lines):
    try:
        with open('gshare_manager.log', 'r') as file:
            log_lines = file.readlines()
        
        trimmed_lines = log_lines[-lines:] if len(log_lines) > lines else log_lines
        
        with open('gshare_manager.log', 'w') as file:
            file.writelines(trimmed_lines)
            
        return jsonify({
            "status": "success", 
            "message": f"로그가 마지막 {lines}줄만 남도록 정리되었습니다.",
            "total_lines": len(trimmed_lines)
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"로그 정리 실패: {str(e)}"}), 500

@app.route('/set_log_level/<string:level>')
def set_log_level(level):
    try:
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        
        if level.upper() not in valid_levels:
            return jsonify({
                "status": "error",
                "message": f"유효하지 않은 로그 레벨입니다. 가능한 레벨: {', '.join(valid_levels)}"
            }), 400

        set_key('.env', 'LOG_LEVEL', level.upper())
        
        return jsonify({
            "status": "success",
            "message": f"로그 레벨이 {level.upper()}로 변경되었습니다. 최대 {Config().CHECK_INTERVAL}초 후 다음 모니터링 루프에서 적용됩니다."
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"로그 레벨 변경 실패: {str(e)}"
        }), 500

@app.route('/get_log_level')
def get_log_level():
    try:
        load_dotenv()
        level = os.getenv('LOG_LEVEL', 'INFO')
            
        return jsonify({
            "status": "success",
            "current_level": level
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"로그 레벨 확인 실패: {str(e)}"
        }), 500

@app.route('/start_vm')
def start_vm():
    try:
        if current_state is None:
            return jsonify({"status": "error", "message": "State not initialized."}), 404

        if current_state.vm_status == '🟢':
            return jsonify({"status": "error", "message": "VM이 이미 실행 중입니다."}), 400

        if gshare_manager.proxmox_api.start_vm():
            return jsonify({"status": "success", "message": "VM 시작이 요청되었습니다."})
        else:
            return jsonify({"status": "error", "message": "VM 시작 실패"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"VM 시작 요청 실패: {str(e)}"}), 500

@app.route('/shutdown_vm')
def shutdown_vm():
    try:
        if current_state is None:
            return jsonify({"status": "error", "message": "State not initialized."}), 404

        if current_state.vm_status == '🔴':
            return jsonify({"status": "error", "message": "VM이 이미 종료되어 있습니다."}), 400
        
        response = requests.post(config.SHUTDOWN_WEBHOOK_URL, timeout=5)
        response.raise_for_status()
        return jsonify({"status": "success", "message": "VM 종료가 요청되었습니다."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"VM 종료 요청 실패: {str(e)}"}), 500

def run_flask_app():
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if __name__ == '__main__':
    logger = setup_logging()
    config = Config()
    
    try:
        logging.info("───────────────────────────────────────────────")
        proxmox_api = ProxmoxAPI(config)
        gshare_manager = GShareManager(config, proxmox_api)
        logging.info(f"VM 상태 - {gshare_manager.proxmox_api.is_vm_running()}")
        logging.info("GShare 관리 시작")
        
        flask_thread = threading.Thread(target=run_flask_app)
        flask_thread.daemon = True
        flask_thread.start()
        
        gshare_manager.monitor()
    except KeyboardInterrupt:
        logging.info("프로그램 종료")
        logging.info("───────────────────────────────────────────────")
    except Exception as e:
        logging.error(f"예상치 못한 오류 발생: {e}")
        logging.info("───────────────────────────────────────────────")
