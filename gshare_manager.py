import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional
import requests
import subprocess
from datetime import datetime
import pytz
import json
from config import Config  # 새로운 import 추가
from dotenv import load_dotenv
import os

@dataclass
class State:
    last_check_time: str
    vm_status: str  # 🔴 (정지), 🟢 (실행 중)
    cpu_usage: float
    folder_size: int
    last_action: str
    low_cpu_count: int
    uptime: str
    last_size_change_time: str
    last_shutdown_time: str
    
    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

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
            logging.debug(f"VM 상태 확인 응답: {response.json()}")
            return response.json()["data"]["status"] == "running"
        except Exception as e:
            logging.error(f"VM 상태 확인 실패: {e}")
            return False    
    
    def get_vm_uptime(self) -> Optional[float]:
        try:
            response = self.session.get(
                f"{self.config.PROXMOX_HOST}/nodes/{self.config.NODE_NAME}/qemu/{self.config.VM_ID}/status/current"
            )
            response.raise_for_status()
            logging.debug(f"VM 부팅 시간 확인 응답: {response.json()}")
            return response.json()["data"]["uptime"]
        except Exception as e:
            logging.error(f"VM 부팅 시간 확인 실패: {e}")
            return None

    def get_cpu_usage(self) -> Optional[float]:
        try:
            response = self.session.get(
                f"{self.config.PROXMOX_HOST}/nodes/{self.config.NODE_NAME}/qemu/{self.config.VM_ID}/status/current"
            )
            response.raise_for_status()
            logging.debug(f"CPU 사용량 확인 응답: {response.json()}")
            return response.json()["data"]["cpu"] * 100
        except Exception as e:
            logging.error(f"CPU 사용량 확인 실패: {e}")
            return None

    def start_vm(self) -> bool:
        try:
            response = self.session.post(
                f"{self.config.PROXMOX_HOST}/nodes/{self.config.NODE_NAME}/qemu/{self.config.VM_ID}/status/start"
            )
            response.raise_for_status()
            logging.debug(f"VM 시작 응답: {response.json()}")
            logging.info("VM 시작 성공")
            return True
        except Exception as e:
            logging.error(f"VM 시작 실패: {e}")
            return False

class FolderMonitor:
    def __init__(self, config: Config):
        self.config = config
        self.get_folder_size_timeout = self.config.GET_FOLDER_SIZE_TIMEOUT
        self.previous_count = self._load_previous_count()
        if self.previous_count == 0:  # 파일에서 불러오기 실패시 현재 개수로 초기화
            self.previous_count = self._get_file_count()

    def _load_previous_count(self) -> int:
        try:
            with open('current_state.json', 'r', encoding='utf-8') as f:
                state = json.loads(f.read())
                count = state.get('folder_size', 0)
                logging.info(f"이전 상태 파일에서 파일 개수 불러옴: {count}개")
                return count
        except FileNotFoundError:
            logging.info("이전 상태 파일이 없습니다.")
            return 0
        except json.JSONDecodeError:
            logging.error("상태 파일 파싱 실패")
            return 0
        except Exception as e:
            logging.error(f"이전 상태 불러오기 실패: {e}")
            return 0

    def _get_file_count(self) -> int:
        try:
            result = subprocess.run(
                ['find', self.config.MOUNT_PATH, '-type', 'f', '-print0' '|', 'wc', '-l', '--files0-from=-'],
                capture_output=True,
                text=True,
                timeout=self.get_folder_size_timeout,
                check=True
            )
            count = int(result.stdout.strip())
            logging.debug(f"현재 파일 개수: {count}개")
            return count
        except subprocess.TimeoutExpired:
            logging.error("파일 개수 확인 시간 초과")
            return self.previous_count
        except (subprocess.SubprocessError, ValueError, IndexError) as e:
            logging.error(f"파일 개수 확인 중 오류 발생: {e}")
            return self.previous_count
        except Exception as e:
            logging.error(f"파일 개수 확인 중 예상치 못한 오류: {e}")
            return self.previous_count

    def has_size_changed(self) -> bool:
        current_count = self._get_file_count()
        if current_count != self.previous_count:
            logging.info(f"파일 개수 변화: {current_count - self.previous_count}개")
            self.previous_count = current_count
            return True
        return False

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
                logging.info(f"종료 웹훅 전송 성공, 업타임: {uptime_str}")
            except Exception as e:
                logging.error(f"종료 웹훅 전송 실패: {e}")
        else:
            logging.info("종료웹훅을 전송하려했지만 vm이 이미 종료상태입니다.")

    def _update_state(self) -> None:
        try:
            current_time = datetime.now(pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
            vm_status = "🟢" if self.proxmox_api.is_vm_running() else "🔴"
            cpu_usage = self.proxmox_api.get_cpu_usage() or 0.0
            folder_size = self.folder_monitor.previous_count
            uptime = self.proxmox_api.get_vm_uptime()
            uptime_str = self._format_uptime(uptime) if uptime is not None else "알 수 없음"

            state = State(
                last_check_time=current_time,
                vm_status=vm_status,
                cpu_usage=round(cpu_usage, 2),
                folder_size=folder_size,
                last_action=self.last_action,
                low_cpu_count=self.low_cpu_count,
                uptime=uptime_str,
                last_size_change_time=self.last_size_change_time,
                last_shutdown_time=self.last_shutdown_time
            )

            with open('current_state.json', 'w', encoding='utf-8') as f:
                f.write(state.to_json())
            logging.debug(f"상태 업데이트: {state.to_json()}")
        except Exception as e:
            logging.error(f"상태 업데이트 실패: {e}")

    def monitor(self) -> None:
        while True:
            try:
                logging.debug("모니터링 루프 시작")
                
                try:
                    if self.folder_monitor.has_size_changed():
                        self.last_size_change_time = datetime.now(pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
                        logging.info(f"VM 시작을 시도합니다: {self.last_size_change_time}")
                        if not self.proxmox_api.is_vm_running():
                            self.last_action = "VM 시작"
                            if self.proxmox_api.start_vm():
                                logging.info("VM 시작 성공")
                            else:
                                logging.error("VM 시작 실패")
                except Exception as e:
                    logging.error(f"파일시스템 용량 모니터링 중 오류: {e}")

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
    file_handler = logging.FileHandler('gshare_manager.log')
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    
    # 로거 설정
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level))
    logger.handlers = []  # 기존 핸들러 제거
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    
    return logger

if __name__ == '__main__':
    logger = setup_logging()
    config = Config()
    
    try:
        logging.info("───────────────────────────────────────────────")
        proxmox_api = ProxmoxAPI(config)
        gshare_manager = GShareManager(config, proxmox_api)
        logging.info(f"초기 정보: VM 상태 - {gshare_manager.proxmox_api.is_vm_running()}")
        logging.info(f"마운트된 폴더 파일 수 - {gshare_manager.folder_monitor.previous_count}개")
        if gshare_manager.folder_monitor.previous_count == 0:
            logging.warning(f"파일시스템 용량이 0입니다. {config.MOUNT_PATH} 경로에 감시 폴더가 정상적으로 마운트되어 있는지 확인하세요.")
        logging.info("GShare 관리 시작")
        gshare_manager.monitor()
    except KeyboardInterrupt:
        logging.info("프로그램 종료")
        logging.info("───────────────────────────────────────────────")
    except Exception as e:
        logging.error(f"예상치 못한 오류 발생: {e}")
        logging.info("───────────────────────────────────────────────")
