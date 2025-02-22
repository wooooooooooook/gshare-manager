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

app = Flask(__name__)

# 전역 변수로 상태와 관리자 객체 선언
current_state = None
gshare_manager = None

@dataclass
class State:
    last_check_time: str
    vm_status: str  # 🔴 (정지), 🟢 (실행 중)
    cpu_usage: float
    folder_size: int  # 실제 바이트 단위 저장
    folder_size_readable: str  # 사람이 읽기 쉬운 형식
    last_action: str
    low_cpu_count: int
    uptime: str
    last_size_change_time: str
    last_shutdown_time: str
    
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
        self.get_folder_size_timeout = self.config.GET_FOLDER_SIZE_TIMEOUT
        self.previous_sizes = {}  # 각 서브폴더별 이전 크기를 저장
        self.active_shares = set()  # 현재 활성화된 SMB 공유 목록
        self._update_subfolder_sizes()
        self._ensure_smb_installed()
        self._init_smb_config()

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

    def _activate_smb_share(self, subfolder: str) -> bool:
        """특정 서브폴더의 SMB 공유를 활성화"""
        try:
            if subfolder in self.active_shares:
                return True

            source_path = os.path.join(self.config.MOUNT_PATH, subfolder)
            share_name = f"{self.config.SMB_SHARE_NAME}_{subfolder}"
            
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

    def _get_subfolders(self) -> list[str]:
        """마운트 경로의 서브폴더 목록을 반환"""
        try:
            subfolders = []
            for item in os.listdir(self.config.MOUNT_PATH):
                full_path = os.path.join(self.config.MOUNT_PATH, item)
                if os.path.isdir(full_path):
                    subfolders.append(item)
            return subfolders
        except Exception as e:
            logging.error(f"서브폴더 목록 가져오기 실패: {e}")
            return []

    def _get_folder_size(self, path: str) -> int:
        """지정된 경로의 폴더 크기를 반환"""
        try:
            cmd = f"du -sb {path} 2>/dev/null | cut -f1"
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.get_folder_size_timeout
            )
            
            if result.returncode != 0:
                logging.error(f"폴더 용량 확인 명령어 실행 실패: {result.stderr}")
                return self.previous_sizes.get(path, 0)
                
            output = result.stdout.strip()
            if not output:
                logging.debug(f"폴더를 찾지 못했습니다: {path}")
                return 0
                
            size = int(output)
            logging.debug(f"현재 폴더 용량 ({path}): {format_size(size)}")
            return size
        except subprocess.TimeoutExpired:
            logging.error(f"폴더 용량 확인 시간 초과 ({path}). NAS가 살아있나요?")
            return self.previous_sizes.get(path, 0)
        except Exception as e:
            logging.error(f"폴더 용량 확인 중 오류 발생 ({path}): {e}")
            return self.previous_sizes.get(path, 0)

    def _update_subfolder_sizes(self) -> None:
        """모든 서브폴더의 크기를 업데이트"""
        subfolders = self._get_subfolders()
        for subfolder in subfolders:
            full_path = os.path.join(self.config.MOUNT_PATH, subfolder)
            if full_path not in self.previous_sizes:
                self.previous_sizes[full_path] = self._get_folder_size(full_path)

    def check_size_changes(self) -> list[str]:
        """크기가 변경된 서브폴더 목록을 반환"""
        changed_folders = []
        self._update_subfolder_sizes()
        
        for path, prev_size in self.previous_sizes.items():
            current_size = self._get_folder_size(path)
            if current_size != prev_size:
                size_diff = current_size - prev_size
                if size_diff > 0:
                    subfolder = os.path.basename(path)
                    logging.info(f"폴더 용량 변화 감지 ({subfolder}): {format_size(size_diff)} 증가 (현재: {format_size(current_size)})")
                    changed_folders.append(subfolder)
                self.previous_sizes[path] = current_size
        
        return changed_folders

    @property
    def total_size(self) -> int:
        """전체 폴더 크기 반환"""
        return sum(self.previous_sizes.values())

class GShareManager:
    def __init__(self, config: Config, proxmox_api: ProxmoxAPI):
        self.config = config
        self.proxmox_api = proxmox_api
        self.low_cpu_count = 0
        self.last_action = "프로그램 시작"
        self.last_size_change_time = "-"
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
            folder_size = self.folder_monitor.total_size
            uptime = self.proxmox_api.get_vm_uptime()
            uptime_str = self._format_uptime(uptime) if uptime is not None else "알 수 없음"

            current_state = State(
                last_check_time=current_time,
                vm_status=vm_status,
                cpu_usage=round(cpu_usage, 2),
                folder_size=folder_size,
                folder_size_readable=format_size(folder_size),
                last_action=self.last_action,
                low_cpu_count=self.low_cpu_count,
                uptime=uptime_str,
                last_size_change_time=self.last_size_change_time,
                last_shutdown_time=self.last_shutdown_time
            )
            logging.debug(f"상태 업데이트: {current_state.to_dict()}")
        except Exception as e:
            logging.error(f"상태 업데이트 실패: {e}")

    def monitor(self) -> None:
        while True:
            try:
                # 매 루프마다 로그 레벨 확인 및 업데이트
                update_log_level()
                
                logging.debug("모니터링 루프 시작")
                
                try:
                    logging.debug("폴더 용량 변화 확인 중")
                    changed_folders = self.folder_monitor.check_size_changes()
                    if changed_folders:
                        self.last_size_change_time = datetime.now(pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
                        
                        # 변경된 폴더들의 SMB 공유 활성화
                        for folder in changed_folders:
                            if self.folder_monitor._activate_smb_share(folder):
                                self.last_action = f"SMB 공유 활성화: {folder}"
                        
                        # VM이 정지 상태인 경우 시작
                        if not self.proxmox_api.is_vm_running():
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
    app.run(host='0.0.0.0', port=5000)

if __name__ == '__main__':
    logger = setup_logging()
    config = Config()
    
    try:
        logging.info("───────────────────────────────────────────────")
        proxmox_api = ProxmoxAPI(config)
        gshare_manager = GShareManager(config, proxmox_api)
        logging.info(f"VM 상태 - {gshare_manager.proxmox_api.is_vm_running()}")
        logging.info(f"폴더 용량 - {format_size(gshare_manager.folder_monitor.total_size)}")
        logging.info("GShare 관리 시작")
        
        # Flask 웹 서버를 별도 스레드에서 실행
        flask_thread = threading.Thread(target=run_flask_app)
        flask_thread.daemon = True  # 메인 프로그램이 종료되면 웹 서버도 종료
        flask_thread.start()
        
        # 메인 모니터링 루프 실행
        gshare_manager.monitor()
    except KeyboardInterrupt:
        logging.info("프로그램 종료")
        logging.info("───────────────────────────────────────────────")
    except Exception as e:
        logging.error(f"예상치 못한 오류 발생: {e}")
        logging.info("───────────────────────────────────────────────")
