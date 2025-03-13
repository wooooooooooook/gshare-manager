import logging
from logging.handlers import RotatingFileHandler
import time
from dataclasses import dataclass, asdict
from typing import Optional
import requests  # type: ignore
import subprocess
from datetime import datetime
import pytz  # type: ignore
import os
import threading
import sys
import atexit
from config import GshareConfig  # type: ignore
from proxmox_api import ProxmoxAPI
from web_server import GshareWebServer
from smb_manager import SMBManager
import yaml  # type: ignore
import traceback
import urllib3  # type: ignore

# SSL 경고 메시지 비활성화
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 전역 변수로 상태와 관리자 객체 선언
gshare_manager = None


@dataclass
class State:
    last_check_time: str
    vm_running: bool
    cpu_usage: float
    last_action: str
    cpu_threshold: float
    low_cpu_count: int
    threshold_count: int
    uptime: str
    last_shutdown_time: str
    monitored_folders: dict
    smb_running: bool
    check_interval: int
    nfs_mounted: bool = False

    def to_dict(self):
        data = asdict(self)
        data['vm_status'] = 'ON' if self.vm_running else 'OFF'
        data['smb_status'] = 'ON' if self.smb_running else 'OFF'
        data['nfs_status'] = 'ON' if self.nfs_mounted else 'OFF'
        return data


class FolderMonitor:
    def __init__(self, config: GshareConfig, proxmox_api: ProxmoxAPI, last_shutdown_time: float):
        self.config = config
        self.proxmox_api = proxmox_api
        self.previous_mtimes = {}  # 각 서브폴더별 이전 수정 시간을 저장
        self.active_links = set()  # 현재 활성화된 심볼릭 링크 목록
        self.last_shutdown_time = last_shutdown_time  # VM 마지막 종료 시간
        self.nfs_uid, self.nfs_gid = self._get_nfs_ownership()
        logging.debug(
            f"NFS 마운트 경로의 UID/GID 확인 완료: {self.nfs_uid}/{self.nfs_gid}")

        # SMB 관리자 초기화
        logging.debug("SMB 관리자 초기화 중...")
        self.smb_manager = SMBManager(config, self.nfs_uid, self.nfs_gid)

        # 서브폴더 정보 업데이트
        logging.debug("서브폴더 정보 업데이트 중...")
        self._update_subfolder_mtimes()

        # 초기 실행 시 마지막 VM 시작 시간 이후에 수정된 폴더들의 링크 생성
        logging.debug("최근 수정된 폴더의 링크 생성 중...")
        self._create_links_for_recently_modified()

    def _get_nfs_ownership(self) -> tuple[int, int]:
        """NFS 마운트 경로의 UID/GID를 반환"""
        # 마운트 경로가 존재하는지 확인
        if not os.path.exists(self.config.MOUNT_PATH):
            error_msg = f"NFS 마운트 경로가 존재하지 않습니다: {self.config.MOUNT_PATH}"
            logging.error(error_msg)
            raise FileNotFoundError(error_msg)

        # 마운트 경로가 디렉토리인지 확인
        if not os.path.isdir(self.config.MOUNT_PATH):
            error_msg = f"NFS 마운트 경로가 디렉토리가 아닙니다: {self.config.MOUNT_PATH}"
            logging.error(error_msg)
            raise NotADirectoryError(error_msg)

        # 마운트 경로에 접근 권한이 있는지 확인
        if not os.access(self.config.MOUNT_PATH, os.R_OK):
            error_msg = f"NFS 마운트 경로에 읽기 권한이 없습니다: {self.config.MOUNT_PATH}"
            logging.error(error_msg)
            raise PermissionError(error_msg)

        # ls -n 명령어로 마운트 경로의 첫 번째 항목의 UID/GID 확인
        try:
            result = subprocess.run(
                ['ls', '-n', self.config.MOUNT_PATH], capture_output=True, text=True, check=True)
            # 출력 결과를 줄 단위로 분리
            lines = result.stdout.strip().split('\n')
            # 첫 번째 파일/디렉토리 정보가 있는 줄 찾기 (total 제외)
            for line in lines:
                if line.startswith('total'):
                    continue
                # 공백으로 분리하여 UID(3번째 필드)와 GID(4번째 필드) 추출
                parts = line.split()
                if len(parts) >= 4:
                    uid = int(parts[2])
                    gid = int(parts[3])
                    logging.debug(f"NFS 마운트 경로의 UID/GID: {uid}/{gid}")
                    return uid, gid

            # 디렉토리가 비어있는 경우
            error_msg = "마운트 경로가 비어있거나 파일/디렉토리 정보를 읽을 수 없습니다. UID/GID를 0/0으로 반환합니다."
            logging.error(error_msg)
            return 0, 0
        except subprocess.CalledProcessError as e:
            error_msg = f"ls 명령 실행 실패: {e}"
            logging.error(error_msg)
            raise RuntimeError(error_msg)

    def _get_subfolders(self) -> list[str]:
        """마운트 경로의 모든 서브폴더를 재귀적으로 반환"""
        start_time = time.time()
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
                            rel_path = os.path.relpath(
                                full_path, self.config.MOUNT_PATH)

                            # 마운트 경로와 같은 경우 또는 숨김 폴더인 경우 제외
                            if rel_path == '.' or rel_path.startswith('.') or '/..' in rel_path:
                                continue

                            # 중복 방지
                            if rel_path not in subfolders:
                                subfolders.append(rel_path)
                        except Exception as e:
                            logging.error(f"서브폴더 처리 중 오류 발생 ({dir_name}): {e}")
            except Exception as e:
                logging.error(f"마운트 경로 스캔 중 오류 발생: {e}")

            elapsed_time = time.time() - start_time
            logging.debug(
                f"마운트 경로 스캔 완료 - 걸린 시간: {elapsed_time:.3f}초, 서브폴더 수: {len(subfolders)}개")
            return subfolders
        except Exception as e:
            logging.error(f"서브폴더 목록 가져오기 중 오류 발생: {e}")
            elapsed_time = time.time() - start_time
            logging.debug(f"마운트 경로 스캔 실패 - 걸린 시간: {elapsed_time:.3f}초")
            return []

    def _get_folder_mtime(self, path: str) -> float:
        """지정된 경로의 폴더 수정 시간을 반환 (UTC 기준)"""
        try:
            full_path = os.path.join(self.config.MOUNT_PATH, path)
            mtime = os.path.getmtime(full_path)
            return mtime
        except Exception as e:
            logging.error(f"폴더 수정 시간 확인 중 오류 발생 ({path}): {e}")
            return self.previous_mtimes.get(path, 0)

    def _update_subfolder_mtimes(self) -> None:
        """모든 서브폴더의 수정 시간을 업데이트하고 삭제된 폴더 제거"""
        start_time = time.time()
        current_subfolders = set(self._get_subfolders())
        previous_subfolders = set(self.previous_mtimes.keys())

        # 새로 생성된 폴더 처리
        new_folders = current_subfolders - previous_subfolders
        for folder in new_folders:
            self.previous_mtimes[folder] = self._get_folder_mtime(folder)
            logging.debug(f"새 폴더 감지: {folder}")

        # 삭제된 폴더 처리
        deleted_folders = previous_subfolders - current_subfolders
        for folder in deleted_folders:
            logging.info(f"폴더 삭제 감지: {folder}")
            # 심볼릭 링크 제거
            if self.smb_manager.remove_symlink(folder):
                self.active_links.discard(folder)
            # 이전 수정 시간 기록에서 삭제
            if folder in self.previous_mtimes:
                del self.previous_mtimes[folder]

        elapsed_time = time.time() - start_time
        logging.debug(
            f"폴더 구조 업데이트 완료 - 걸린 시간: {elapsed_time:.3f}초, 총 폴더: {len(current_subfolders)}개, 새 폴더: {len(new_folders)}개, 삭제된 폴더: {len(deleted_folders)}개")

    def check_modifications(self) -> tuple[list[str], bool]:
        """수정 시간이 변경된 서브폴더 목록과 VM 시작 필요 여부를 반환"""
        start_time = time.time()
        changed_folders = []
        should_start_vm = False
        self._update_subfolder_mtimes()

        for path, prev_mtime in self.previous_mtimes.items():
            current_mtime = self._get_folder_mtime(path)
            if current_mtime != prev_mtime:
                last_modified = datetime.fromtimestamp(current_mtime, pytz.timezone(
                    self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
                logging.info(f"폴더 수정 시간 변화 감지 ({path}): {last_modified}")
                changed_folders.append(path)
                self.previous_mtimes[path] = current_mtime

                # 심볼릭 링크 생성
                if self.smb_manager.create_symlink(path):
                    self.active_links.add(path)

                # VM 마지막 시작 시간보다 수정 시간이 더 최근인 경우
                if current_mtime > self.last_shutdown_time:
                    should_start_vm = True
                    logging.info(f"VM 시작 조건 충족 - 수정 시간: {last_modified}")

        elapsed_time = time.time() - start_time
        logging.debug(
            f"폴더 수정 시간 확인 완료 - 걸린 시간: {elapsed_time:.3f}초, 변경된 폴더: {len(changed_folders)}개")

        return changed_folders, should_start_vm

    def get_monitored_folders(self) -> dict:
        """감시 중인 모든 폴더와 수정 시간, 링크 상태를 반환"""
        folder_times = []
        for path in self.previous_mtimes.keys():
            mtime = self._get_folder_mtime(path)
            folder_times.append((path, mtime))

        folder_times.sort(key=lambda x: x[1], reverse=True)

        monitored_folders = {}
        for path, mtime in folder_times:
            monitored_folders[path] = {
                'mtime': datetime.fromtimestamp(mtime, pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S'),
                'is_mounted': path in self.active_links
            }

        return monitored_folders

    def _create_links_for_recently_modified(self) -> None:
        """마지막 VM 시작 시간 이후에 수정된 폴더들의 링크 생성"""
        try:
            recently_modified = []
            for path, mtime in self.previous_mtimes.items():
                if mtime > self.last_shutdown_time:
                    recently_modified.append(path)
                    last_modified = datetime.fromtimestamp(mtime, pytz.timezone(
                        self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
                    logging.info(f"최근 수정된 폴더 감지 ({path}): {last_modified}")

            if recently_modified:
                logging.info(
                    f"마지막 VM 종료({datetime.fromtimestamp(self.last_shutdown_time, pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')}) 이후 수정된 폴더 {len(recently_modified)}개의 링크를 생성합니다.")
                for folder in recently_modified:
                    if self.smb_manager.create_symlink(folder):
                        self.active_links.add(folder)
                        logging.debug(f"초기 링크 생성 성공: {folder}")
                    else:
                        logging.error(f"초기 링크 생성 실패: {folder}")

                # SMB 공유 활성화
                self.smb_manager.activate_smb_share()

                # VM이 정지 상태이고 최근 수정된 파일이 있는 경우 VM 시작
                if not self.proxmox_api.is_vm_running():
                    logging.info("최근 수정된 폴더가 있어 VM을 시작합니다.")
                    if self.proxmox_api.start_vm():
                        logging.info("VM 시작 성공")
                    else:
                        logging.error("VM 시작 실패")
        except Exception as e:
            logging.error(f"최근 수정된 폴더 링크 생성 중 오류 발생: {e}")

    def check_nfs_status(self) -> bool:
        """NFS 마운트가 되어 있는지 확인"""
        try:
            if not self.config.NFS_PATH or not self.config.MOUNT_PATH:
                return False

            # mount 명령어로 현재 마운트된 NFS 리스트 확인
            mount_check = subprocess.run(
                ['mount', '-t', 'nfs'], capture_output=True, text=True)

            # NFS_PATH와 MOUNT_PATH가 모두 출력에 있으면 마운트된 것으로 판단
            return self.config.NFS_PATH in mount_check.stdout and self.config.MOUNT_PATH in mount_check.stdout
        except Exception as e:
            logging.error(f"NFS 마운트 상태 확인 중 오류: {e}")
            return False

    def cleanup_resources(self) -> None:
        """리소스 정리: 활성 심볼릭 링크 제거"""
        # 모든 링크 제거
        self.smb_manager.cleanup_all_symlinks()
        self.active_links.clear()
        logging.debug("모든 리소스가 정리되었습니다.")


class GShareManager:
    def __init__(self, config: GshareConfig, proxmox_api: ProxmoxAPI):
        self.config = config
        self.proxmox_api = proxmox_api
        self.low_cpu_count = 0
        self.last_action = "프로그램 시작"
        self.restart_required = False
        self.current_state = self._update_state()  # current_state 인스턴스 변수 추가

        # VM 마지막 종료 시간 로드
        self.last_shutdown_time = self._load_last_shutdown_time()

        # NFS 마운트 먼저 시도
        self._mount_nfs()

        # FolderMonitor 초기화 (NFS 마운트 이후에 수행)
        logging.debug("FolderMonitor 초기화 중...")
        self.folder_monitor = FolderMonitor(
            config, proxmox_api, self.last_shutdown_time)
        self.last_shutdown_time_str = datetime.fromtimestamp(
            self.last_shutdown_time, pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
        logging.debug("FolderMonitor 초기화 완료")

        # SMBManager 초기화 (FolderMonitor의 SMBManager를 사용)
        self.smb_manager = self.folder_monitor.smb_manager
        logging.debug("SMBManager 참조 완료")

    def _load_last_shutdown_time(self) -> float:
        """VM 마지막 종료 시간을 로드 (UTC 기준)"""
        try:
            shutdown_file_path = '/config/.last_shutdown'
            if os.path.exists(shutdown_file_path):
                with open(shutdown_file_path, 'r') as f:
                    return float(f.read().strip())
            else:
                # 파일이 없는 경우 현재 UTC 시간을 저장하고 반환
                current_time = datetime.now(pytz.UTC).timestamp()
                with open(shutdown_file_path, 'w') as f:
                    f.write(str(current_time))
                logging.debug(
                    f"VM 종료 시간 파일이 없어 현재 시간으로 생성: {datetime.fromtimestamp(current_time, pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')}")
                return current_time
        except Exception as e:
            # 오류 발생 시 현재 UTC 시간 사용
            current_time = datetime.now(pytz.UTC).timestamp()
            logging.error(f"VM 마지막 종료 시간 로드 실패: {e}, 현재 시간을 사용합니다.")
            return current_time

    def _mount_nfs(self):
        """NFS 마운트 시도"""
        try:
            if not self.config.NFS_PATH:
                logging.warning("NFS 경로가 설정되지 않았습니다.")
                return

            mount_path = self.config.MOUNT_PATH
            nfs_path = self.config.NFS_PATH

            # 마운트 디렉토리가 없으면 생성
            if not os.path.exists(mount_path):
                os.makedirs(mount_path, exist_ok=True)
                logging.debug(f"마운트 디렉토리 생성: {mount_path}")

            # 이미 마운트되어 있는지 확인
            mount_check = subprocess.run(
                ['mount', '-t', 'nfs'], capture_output=True, text=True)
            if nfs_path in mount_check.stdout and mount_path in mount_check.stdout:
                logging.debug(
                    f"NFS가 이미 마운트되어 있습니다: {nfs_path} -> {mount_path}")
                return

            # NFS 마운트 시도
            logging.debug(f"NFS 마운트 시도: {nfs_path} -> {mount_path}")
            mount_cmd = ['mount', '-t', 'nfs', '-o',
                         'nolock,vers=3,soft,timeo=100', nfs_path, mount_path]
            result = subprocess.run(mount_cmd, capture_output=True, text=True)

            if result.returncode == 0:
                logging.info(f"NFS 마운트 성공: {nfs_path} -> {mount_path}")
            else:
                logging.error(f"NFS 마운트 실패: {result.stderr}")
        except Exception as e:
            logging.error(f"NFS 마운트 중 오류 발생: {e}")

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
                response = requests.post(
                    self.config.SHUTDOWN_WEBHOOK_URL, timeout=5)
                response.raise_for_status()

                uptime = self.proxmox_api.get_vm_uptime()
                uptime_str = self._format_uptime(
                    uptime) if uptime is not None else "알 수 없음"

                # VM 종료 시간 저장
                self.save_last_shutdown_time()

                # VM 종료 시 모든 SMB 공유 비활성화
                self.smb_manager.deactivate_smb_share()
                self.folder_monitor.active_links.clear()

                logging.info(f"종료 웹훅 전송 성공, 업타임: {uptime_str}")
            except Exception as e:
                logging.error(f"종료 웹훅 전송 실패: {e}")
        else:
            logging.info("종료 웹훅을 전송하려했지만 vm이 이미 종료상태입니다.")

    def _update_state(self) -> State:
        try:
            # 매번 호출 시 현재 시간을 새로 계산
            current_time = datetime.now(pytz.timezone(self.config.TIMEZONE))
            current_time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
            logging.debug(
                f"상태 업데이트 - 현재 시간: {current_time_str}, 타임스탬프: {current_time.timestamp()}")

            vm_running = self.proxmox_api.is_vm_running()
            cpu_usage = self.proxmox_api.get_cpu_usage() or 0.0
            uptime = self.proxmox_api.get_vm_uptime()
            uptime_str = self._format_uptime(
                uptime) if uptime is not None else "알 수 없음"

            current_state = State(
                last_check_time=current_time_str,
                vm_running=vm_running,
                cpu_usage=round(cpu_usage, 2),
                last_action=self.last_action,
                cpu_threshold=self.config.CPU_THRESHOLD,
                low_cpu_count=self.low_cpu_count,
                threshold_count=self.config.THRESHOLD_COUNT,
                uptime=uptime_str,
                last_shutdown_time=self.last_shutdown_time_str,
                monitored_folders=self.folder_monitor.get_monitored_folders(),
                smb_running=self.smb_manager.check_smb_status(),
                check_interval=self.config.CHECK_INTERVAL,
                nfs_mounted=self.folder_monitor.check_nfs_status()
            )
            return current_state
        except Exception as e:
            logging.error(f"상태 업데이트 실패: {e}")
            # 현재 시간을 다시 계산
            current_time_str = datetime.now(pytz.timezone(
                self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
            return State(
                last_check_time=current_time_str,
                vm_running=False,
                cpu_usage=0.0,
                cpu_threshold=0.0,
                last_action="상태 업데이트 실패",
                low_cpu_count=0,
                threshold_count=0,
                uptime="알 수 없음",
                last_shutdown_time="-",
                monitored_folders={},
                smb_running=False,
                check_interval=60  # 기본값 60초
            )

    def monitor(self) -> None:
        last_vm_status = None  # VM 상태 변화 감지를 위한 변수
        count = 0
        while True:
            try:
                update_log_level()
                logging.debug(f"모니터링 루프 Count:{count}")
                count += 1
                # VM 상태 확인
                current_vm_status = self.proxmox_api.is_vm_running()

                # VM 상태가 변경되었고, 현재 종료 상태인 경우
                if last_vm_status is not None and last_vm_status != current_vm_status and not current_vm_status:
                    logging.info("VM이 종료되어 SMB 공유를 비활성화합니다.")
                    self.smb_manager.deactivate_smb_share()
                    self.folder_monitor.active_links.clear()

                last_vm_status = current_vm_status

                try:
                    logging.debug("폴더 수정 시간 변화 확인 중")
                    changed_folders, should_start_vm = self.folder_monitor.check_modifications()
                    if changed_folders:
                        # 변경된 폴더들의 SMB 공유 활성화
                        if self.smb_manager.activate_smb_share():
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
                                logging.debug(
                                    f"낮은 CPU 사용량 카운트: {self.low_cpu_count}/{self.config.THRESHOLD_COUNT}")
                                if self.low_cpu_count >= self.config.THRESHOLD_COUNT:
                                    self.last_action = "종료 웹훅 전송"
                                    self._send_shutdown_webhook()
                                    self.low_cpu_count = 0
                            else:
                                self.low_cpu_count = 0
                except Exception as e:
                    logging.error(f"VM 모니터링 중 오류: {e}")

                try:
                    # 상태 업데이트 (웹서버용)
                    self.current_state = self._update_state()  # 인스턴스 변수와 전역 변수 모두 업데이트
                    # 상태 업데이트 로깅 추가
                    logging.debug(
                        f"상태 업데이트 완료 - last_check_time: {self.current_state.last_check_time}")
                except Exception as e:
                    logging.error(f"상태 업데이트 중 오류: {e}")

                time.sleep(self.config.CHECK_INTERVAL)

            except Exception as e:
                logging.error(f"모니터링 루프에서 예상치 못한 오류 발생: {e}")
                time.sleep(self.config.CHECK_INTERVAL)  # 오류 발생시에도 대기 후 계속 실행

    def save_last_shutdown_time(self) -> None:
        """현재 시간을 VM 마지막 종료 시간으로 저장 (UTC 기준)"""
        try:
            # UTC 기준 현재 시간
            current_time = datetime.now(pytz.UTC).timestamp()
            shutdown_file_path = '/config/.last_shutdown'
            with open(shutdown_file_path, 'w') as f:
                f.write(str(current_time))
            self.last_shutdown_time = current_time
            # 폴더 모니터의 last_shutdown_time도 업데이트
            if hasattr(self, 'folder_monitor') and self.folder_monitor is not None:
                self.folder_monitor.last_shutdown_time = current_time
            self.last_shutdown_time_str = datetime.fromtimestamp(
                current_time, pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
            logging.info(f"VM 종료 시간 저장됨: {self.last_shutdown_time_str}")
        except Exception as e:
            logging.error(f"VM 종료 시간 저장 실패: {e}")


def setup_logging():
    """기본 로깅 설정을 초기화"""
    # config.yaml에서 로그 레벨 확인
    yaml_path = '/config/config.yaml'
    log_level = 'INFO'  # 기본값

    try:
        if os.path.exists(yaml_path):
            with open(yaml_path, 'r', encoding='utf-8') as f:
                yaml_config = yaml.safe_load(f)
            if yaml_config and 'log_level' in yaml_config:
                log_level = yaml_config['log_level']
        else:
            # 없으면 환경 변수에서 가져옴
            log_level = os.getenv('LOG_LEVEL', 'INFO')
    except Exception as e:
        print(f"로그 레벨 로드 실패: {e}")
        log_level = os.getenv('LOG_LEVEL', 'INFO')

    # 환경 변수 설정 (다른 모듈에서 참조할 수 있도록)
    os.environ['LOG_LEVEL'] = log_level

    # 로그 디렉토리 확인 및 생성
    log_dir = '/logs'
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'gshare_manager.log')

    # 로거 및 포맷터 설정
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # 기본 시간대를 'Asia/Seoul'로 설정
    formatter.converter = lambda *args: datetime.now(
        tz=pytz.timezone('Asia/Seoul')).timetuple()

    # 루트 로거 설정
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level))

    # 기존 핸들러 제거
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # 콘솔 출력용 핸들러
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # 파일 로깅 핸들러
    try:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5*1024*1024,  # 5MB
            backupCount=3,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        logger.error(f"로그 파일 설정 중 오류 발생: {e}")

    return logger


def update_log_level():
    """로그 레벨을 동적으로 업데이트"""

    log_level = os.getenv('LOG_LEVEL', 'INFO')

    # 로거 레벨 설정
    logging.getLogger().setLevel(getattr(logging, log_level))

    # 모든 로거에 동일한 레벨 적용 (추가)
    for logger_name in logging.root.manager.loggerDict:
        logging.getLogger(logger_name).setLevel(getattr(logging, log_level))

    # urllib3와 requests 라이브러리의 로깅 레벨은 항상 WARNING으로 유지
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
    logging.getLogger('urllib3.connection').setLevel(logging.WARNING)
    logging.getLogger('urllib3.poolmanager').setLevel(logging.WARNING)
    logging.getLogger('urllib3.util.retry').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('requests.packages.urllib3').setLevel(logging.WARNING)


def update_timezone(timezone='Asia/Seoul'):
    """로깅 시간대 설정 업데이트"""
    try:
        # 모든 핸들러의 포맷터 업데이트
        for handler in logging.getLogger().handlers:
            if handler.formatter:
                handler.formatter.converter = lambda *args: datetime.now(
                    tz=pytz.timezone(timezone)).timetuple()
        logging.debug(f"로깅 시간대가 '{timezone}'으로 업데이트되었습니다.")
    except Exception as e:
        logging.error(f"로깅 시간대 업데이트 중 오류 발생: {e}")


def check_config_complete():
    """설정이 완료되었는지 확인"""
    logging.debug("설정 완료 여부 확인 시작")
    try:
        # 모든 실행이 Docker 환경에서 이루어진다고 가정
        config_path = '/config/config.yaml'
        init_flag_path = '/config/.init_complete'

        # 초기화 완료 플래그 확인
        init_flag_exists = os.path.exists(init_flag_path)

        # 설정 파일 존재 확인
        config_exists = os.path.exists(config_path)

        # 두 파일 중 하나라도 없으면 설정이 완료되지 않은 것으로 판단
        if not config_exists or not init_flag_exists:
            logging.info("설정 파일 또는 초기화 플래그가 없습니다. 설정이 완료되지 않았습니다.")
            return False

        # 파일 수정 시간 비교
        try:
            # config.yaml 파일의 수정 시간
            config_mtime = os.path.getmtime(config_path)
            config_mtime_str = datetime.fromtimestamp(
                config_mtime).strftime('%Y-%m-%d %H:%M:%S')

            # init_flag 파일의 내용에서 시간 읽기
            with open(init_flag_path, 'r') as f:
                init_flag_time_str = f.read().strip()

            # init_flag 파일이 비어있거나 형식이 잘못된 경우
            if not init_flag_time_str:
                logging.warning("초기화 플래그 파일이 비어있습니다.")
                return False

            try:
                # 문자열을 datetime 객체로 변환
                init_flag_time = datetime.strptime(
                    init_flag_time_str, '%Y-%m-%d %H:%M:%S')
                config_time = datetime.fromtimestamp(config_mtime)

                # 초기화 플래그 시간이 config 파일 수정 시간보다 이후인지 확인
                is_valid = init_flag_time >= config_time

                if not is_valid:
                    logging.debug("설정 파일이 초기화 완료 이후에 수정되었습니다. 재설정이 필요합니다.")
                    return False

            except ValueError as e:
                logging.error(f"초기화 플래그 시간 형식 오류: {e}")
                # 시간 형식 오류의 경우, 플래그 파일을 업데이트
                with open(init_flag_path, 'w') as f:
                    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    f.write(current_time)
                logging.debug(f"초기화 플래그 시간을 현재 시간({current_time})으로 업데이트했습니다.")
                return True

        except Exception as e:
            logging.error(f"파일 시간 비교 중 오류 발생: {e}")
            return False

        # 모든 조건을 통과하면 설정 완료로 판단
        logging.debug("설정 완료 여부 확인 완료")
        return True

    except Exception as e:
        logging.error(f"설정 완료 여부 확인 중 오류 발생: {e}")
        return False


if __name__ == "__main__":
    # 초기 로깅 설정
    setup_logging()
    logging.info("──────────────────────────────────────────────────")
    logging.info("GShare 애플리케이션 시작")
    logging.info("──────────────────────────────────────────────────")

    gshare_web_server = GshareWebServer()
    try:
        # 초기화 완료 플래그 경로
        init_flag_path = '/config/.init_complete'

        # 설정 완료 여부 확인
        setup_completed = check_config_complete()

        # 설정이 완료되지 않았으면 랜딩 페이지로
        if not setup_completed:
            logging.info("랜딩 페이지 진입")
            # 랜딩 페이지 실행
            gshare_web_server.run_landing_page()
            sys.exit(0)

        try:
            # 설정 로드
            logging.debug("설정 파일 로드 중...")
            config = GshareConfig.load_config()
            logging.debug("설정 파일 로드 완료")

            # 시간대 설정
            logging.debug(f"시간대 설정: {config.TIMEZONE}")
            os.environ['TZ'] = config.TIMEZONE
            try:
                time.tzset()  # type: ignore
            except AttributeError:
                # Windows에서는 tzset()을 지원하지 않음
                logging.info("Windows 환경에서는 tzset()이 지원되지 않습니다.")
                pass

            # 로깅 시간대 업데이트
            update_timezone(config.TIMEZONE)

            # 객체 초기화
            logging.debug("Proxmox API 객체 초기화 중...")
            try:
                proxmox_api = ProxmoxAPI(config)
                logging.debug("Proxmox API 객체 초기화 완료")
            except Exception as api_error:
                logging.error(f"Proxmox API 객체 초기화 중 오류 발생: {api_error}")
                logging.error(f"상세 오류: {traceback.format_exc()}")
                raise
            logging.debug("GShareManager 객체 초기화 중...")
            try:
                gshare_manager = GShareManager(config, proxmox_api)
                logging.debug("GShareManager 객체 초기화 완료")
            except Exception as manager_error:
                logging.error(f"GShareManager 객체 초기화 중 오류 발생: {manager_error}")
                logging.error(f"상세 오류: {traceback.format_exc()}")
                raise

            # VM 상태 확인
            try:
                vm_running = gshare_manager.proxmox_api.is_vm_running()
                logging.info(f"현재 VM 실행 상태: {vm_running}")
            except Exception as vm_error:
                logging.error(f"VM 상태 확인 중 오류 발생: {vm_error}")
                logging.error(f"상세 오류: {traceback.format_exc()}")
                # VM 상태 확인 오류는 치명적이지 않을 수 있으므로 계속 진행

            # 명확한 시작 로그 메시지 추가
            logging.info("──────────────────────────────────────────────────")
            logging.info("GShare 관리 시작")
            logging.info("──────────────────────────────────────────────────")

            # Flask 앱 초기화 및 상태 전달
            logging.debug("웹 서버 초기화 중...")
            try:
                # 초기 상태 업데이트
                gshare_web_server.set_config(config)
                gshare_web_server.set_manager(gshare_manager)
                gshare_web_server.init_server()
                logging.debug("웹 서버 초기화 완료")
            except Exception as server_error:
                logging.error(f"웹 서버 초기화 중 오류 발생: {server_error}")
                logging.error(f"상세 오류: {traceback.format_exc()}")
                raise

            # Flask 스레드 시작
            logging.debug("Flask 웹 서버 시작 중...")
            try:
                flask_thread = threading.Thread(
                    target=gshare_web_server.run_flask_app)
                flask_thread.daemon = True
                flask_thread.start()
                logging.debug("Flask 웹 서버 시작 완료")
            except Exception as flask_error:
                logging.error(f"Flask 웹 서버 시작 중 오류 발생: {flask_error}")
                logging.error(f"상세 오류: {traceback.format_exc()}")
                raise

            # 모니터링 시작
            logging.info(f"모니터링 시작... 간격: {config.CHECK_INTERVAL}초")
            gshare_manager.monitor()

        except Exception as e:
            logging.error(f"애플리케이션 초기화 중 심각한 오류 발생: {e}")
            logging.error(f"상세 오류: {traceback.format_exc()}")
            logging.error("──────────────────────────────────────────────────")
            logging.error("앱이 오류로 인해 정상적으로 시작되지 못했습니다. 로그를 확인하세요.")
            logging.error("──────────────────────────────────────────────────")
            # 오류가 발생해도 Flask 서버는 계속 실행
            while True:
                time.sleep(60)  # 1분마다 체크

    except KeyboardInterrupt:
        logging.info("프로그램 종료")
        logging.info("───────────────────────────────────────────────")
    except Exception as e:
        logging.error(f"예상치 못한 오류 발생: {e}")
        logging.error(f"상세 오류: {traceback.format_exc()}")
        logging.info("───────────────────────────────────────────────")
        # 오류가 발생해도 Flask 서버는 계속 실행
        while True:
            time.sleep(60)  # 1분마다 체크

# 프로그램 종료 시 호출
atexit.register(lambda: gshare_manager.folder_monitor.cleanup_resources(
) if gshare_manager else None)
