import logging
from logging.handlers import RotatingFileHandler
import time
from dataclasses import dataclass, asdict
from typing import Optional
import requests
import subprocess
from datetime import datetime
import pytz
from config import Config
from dotenv import load_dotenv, set_key
import os
from flask import Flask, jsonify, render_template
import threading
import sys
import atexit

cli = sys.modules['flask.cli']
cli.show_server_banner = lambda *x: None
app = Flask(__name__)
app.logger.disabled = True
log = logging.getLogger('werkzeug')
log.disabled = True

# 전역 변수로 상태와 관리자 객체 선언
current_state = None
gshare_manager = None

@dataclass
class State:
    last_check_time: str
    vm_running: bool
    cpu_usage: float
    last_action: str
    low_cpu_count: int
    uptime: str
    last_shutdown_time: str
    monitored_folders: dict
    last_vm_start_time: str
    smb_running: bool
    
    def to_dict(self):
        data = asdict(self)
        data['vm_status'] = 'ON' if self.vm_running else 'OFF'
        data['smb_status'] = 'ON' if self.smb_running else 'OFF'
        return data

class ProxmoxAPI:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.verify = False
        self._set_token_auth()
        self.folder_monitor = None

    def _set_token_auth(self) -> None:
        # API 토큰을 사용하여 인증 헤더 설정
        self.session.headers.update({
            "Authorization": f"PVEAPIToken={self.config.TOKEN_ID}={self.config.SECRET}"
        })
        logging.info("Proxmox API 토큰 인증 설정 완료")
        self.session.timeout = (5, 10)  # (connect timeout, read timeout)

    def set_folder_monitor(self, folder_monitor):
        """FolderMonitor 인스턴스 설정"""
        self.folder_monitor = folder_monitor

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
            
            if self.folder_monitor:
                self.folder_monitor.update_vm_start_time()
                
            return True
        except Exception as e:
            return False

class FolderMonitor:
    def __init__(self, config: Config, proxmox_api: ProxmoxAPI):
        self.config = config
        self.proxmox_api = proxmox_api
        self.previous_mtimes = {}  # 각 서브폴더별 이전 수정 시간을 저장
        self.active_links = set()  # 현재 활성화된 심볼릭 링크 목록
        self.last_vm_start_time = self._load_last_vm_start_time()  # VM 마지막 시작 시간
        
        # 공유용 링크 디렉토리 생성
        self.links_dir = "/mnt/gshare_links"
        self._ensure_links_directory()
        
        # 시작 시 기존 심볼릭 링크 모두 제거
        try:
            if os.path.exists(self.links_dir):
                for filename in os.listdir(self.links_dir):
                    file_path = os.path.join(self.links_dir, filename)
                    if os.path.islink(file_path):
                        try:
                            os.remove(file_path)
                            logging.debug(f"기존 심볼릭 링크 제거: {file_path}")
                        except Exception as e:
                            logging.error(f"심볼릭 링크 제거 실패 ({file_path}): {e}")
        except Exception as e:
            logging.error(f"기존 심볼릭 링크 제거 중 오류 발생: {e}")
        
        self._update_subfolder_mtimes()
        
        # SMB 관련 초기 설정
        self._ensure_smb_installed()
        self._init_smb_config()
        
        # NFS 마운트 경로의 UID/GID 확인
        self.nfs_uid, self.nfs_gid = self._get_nfs_ownership()
        logging.debug(f"NFS 마운트 경로의 UID/GID: {self.nfs_uid}/{self.nfs_gid}")
        
        # SMB 사용자의 UID/GID 설정 (서비스는 시작하지 않음)
        self._set_smb_user_ownership(start_service=False)
        
        # 초기 실행 시 마지막 VM 시작 시간 이후에 수정된 폴더들의 링크 생성
        self._create_links_for_recently_modified()

    def _load_last_vm_start_time(self) -> float:
        """VM 마지막 시작 시간을 로드"""
        try:
            if os.path.exists('last_vm_start.txt'):
                with open('last_vm_start.txt', 'r') as f:
                    return float(f.read().strip())
            else:
                # 파일이 없는 경우 현재 시간을 저장하고 반환
                current_time = time.time()
                with open('last_vm_start.txt', 'w') as f:
                    f.write(str(current_time))
                logging.info(f"VM 시작 시간 파일이 없어 현재 시간으로 생성: {datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S')}")
                return current_time
        except Exception as e:
            # 오류 발생 시 현재 시간 사용
            current_time = time.time()
            logging.error(f"VM 마지막 시작 시간 로드 실패: {e}, 현재 시간을 사용합니다.")
            return current_time

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
   # 심볼릭 링크 설정
   follow symlinks = yes
   wide links = yes
   unix extensions = no
   allow insecure wide links = yes
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
        """지정된 경로의 폴더 수정 시간을 반환 (UTC -> KST 변환)"""
        try:
            full_path = os.path.join(self.config.MOUNT_PATH, path)
            utc_time = os.path.getmtime(full_path)
            # UTC -> KST 변환 (9시간 추가)
            kst_time = utc_time + (9 * 3600)  # 9시간을 초 단위로 추가
            return kst_time
        except Exception as e:
            logging.error(f"폴더 수정 시간 확인 중 오류 발생 ({path}): {e}")
            return self.previous_mtimes.get(path, 0)

    def _update_subfolder_mtimes(self) -> None:
        """모든 서브폴더의 수정 시간을 업데이트하고 삭제된 폴더 제거"""
        current_subfolders = set(self._get_subfolders())
        previous_subfolders = set(self.previous_mtimes.keys())
        
        # 새로 생성된 폴더 처리
        new_folders = current_subfolders - previous_subfolders
        for folder in new_folders:
            mtime = self._get_folder_mtime(folder)
            self.previous_mtimes[folder] = mtime
        
        # 삭제된 폴더 처리
        deleted_folders = previous_subfolders - current_subfolders
        for folder in deleted_folders:
            del self.previous_mtimes[folder]
            # SMB 공유도 비활성화
            if folder in self.active_links:
                self._remove_symlink(folder)
        
        # 기존 폴더 업데이트 (삭제되지 않은 폴더만)
        for folder in current_subfolders & previous_subfolders:
            try:
                full_path = os.path.join(self.config.MOUNT_PATH, folder)
                if os.path.exists(full_path):
                    if not os.access(full_path, os.R_OK):
                        logging.warning(f"폴더 접근 권한 없음: {folder}")
                        continue
            except Exception as e:
                logging.error(f"폴더 상태 확인 중 오류 발생 ({folder}): {e}")
                continue

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
                
                # 심볼릭 링크 생성
                self._create_symlink(path)
                
                # VM 마지막 시작 시간보다 수정 시간이 더 최근인 경우
                if current_mtime > self.last_vm_start_time:
                    should_start_vm = True
                    logging.info(f"VM 시작 조건 충족 - 수정 시간: {last_modified}")
        
        return changed_folders, should_start_vm

    def update_vm_start_time(self) -> None:
        """VM이 시작될 때 호출하여 시작 시간을 업데이트"""
        self._save_last_vm_start_time()

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
                'mtime': datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'is_mounted': path in self.active_links
            }
        
        return monitored_folders

    def _ensure_links_directory(self):
        """공유용 링크 디렉토리 생성"""
        try:
            if not os.path.exists(self.links_dir):
                os.makedirs(self.links_dir)
                logging.info(f"공유용 링크 디렉토리 생성됨: {self.links_dir}")
            
            # 디렉토리 권한 설정
            os.chmod(self.links_dir, 0o755)
            subprocess.run(['sudo', 'chown', f"{self.config.SMB_USERNAME}:{self.config.SMB_USERNAME}", self.links_dir], check=True)
            logging.info("공유용 링크 디렉토리 권한 설정 완료")
        except Exception as e:
            logging.error(f"공유용 링크 디렉토리 생성/설정 실패: {e}")
            raise

    def _set_smb_user_ownership(self, start_service: bool = True) -> None:
        """SMB 사용자의 UID/GID를 NFS 마운트 경로와 동일하게 설정"""
        try:
            # 현재 SMB 사용자의 UID/GID 확인
            try:
                user_info = subprocess.run(['id', self.config.SMB_USERNAME], capture_output=True, text=True)
                if user_info.returncode == 0:
                    # 출력 형식: uid=1000(user) gid=1000(user) groups=1000(user)
                    info_parts = user_info.stdout.strip().split()
                    current_uid = int(info_parts[0].split('=')[1].split('(')[0])
                    current_gid = int(info_parts[1].split('=')[1].split('(')[0])
                    
                    # UID/GID가 이미 일치하는 경우
                    if current_uid == self.nfs_uid and current_gid == self.nfs_gid:
                        logging.info(f"SMB 사용자와 NFS의 UID/GID 일치 확인. (UID: {current_uid}, GID: {current_gid})")
                        # 서비스 시작이 요청된 경우에만 서비스 시작
                        if start_service:
                            subprocess.run(['sudo', 'systemctl', 'start', 'smbd'], check=True)
                            subprocess.run(['sudo', 'systemctl', 'start', 'nmbd'], check=True)
                        return
            except subprocess.CalledProcessError:
                pass  # 사용자가 없는 경우 계속 진행
            
            # 먼저 Samba 서비스 중지
            logging.debug("Samba 서비스 중지 시도...")
            subprocess.run(['sudo', 'systemctl', 'stop', 'smbd'], check=True)
            subprocess.run(['sudo', 'systemctl', 'stop', 'nmbd'], check=True)
            logging.debug("Samba 서비스 중지 완료")
            
            # 그룹 존재 여부 확인
            try:
                group_info = subprocess.run(['getent', 'group', str(self.nfs_gid)], 
                                         capture_output=True, text=True).stdout.strip()
                if group_info:
                    # GID가 이미 사용 중이면 해당 그룹 사용
                    existing_group = group_info.split(':')[0]
                    self.config.SMB_USERNAME = existing_group
                else:
                    # 그룹이 없으면 생성
                    try:
                        subprocess.run(['sudo', 'groupadd', '-g', str(self.nfs_gid), self.config.SMB_USERNAME], check=True)
                        logging.info(f"그룹 '{self.config.SMB_USERNAME}' 생성됨 (GID: {self.nfs_gid})")
                    except subprocess.CalledProcessError as e:
                        if e.returncode == 4:  # GID already exists
                            # 다른 GID로 시도
                            subprocess.run(['sudo', 'groupadd', self.config.SMB_USERNAME], check=True)
                            logging.warning(f"GID {self.nfs_gid}가 사용 중이어서 시스템이 할당한 GID로 그룹을 생성했습니다. 공유 폴더에 권한이 없을 수 있습니다.")
            except subprocess.CalledProcessError as e:
                logging.error(f"그룹 정보 확인 실패: {e}")
                raise

            # 사용자 존재 여부 확인
            user_exists = subprocess.run(['id', self.config.SMB_USERNAME], capture_output=True).returncode == 0
            
            if not user_exists:
                # 사용자가 없으면 생성
                logging.info(f"사용자 '{self.config.SMB_USERNAME}' 생성...")
                try:
                    subprocess.run([
                        'sudo', 'useradd',
                        '-u', str(self.nfs_uid),
                        '-g', self.config.SMB_USERNAME,
                        '-M',  # 홈 디렉토리 생성하지 않음
                        '-s', '/sbin/nologin',  # 로그인 셸 비활성화
                        self.config.SMB_USERNAME
                    ], check=True)
                except subprocess.CalledProcessError as e:
                    if e.returncode == 4:  # UID already exists
                        # 다른 UID로 시도
                        subprocess.run([
                            'sudo', 'useradd',
                            '-g', self.config.SMB_USERNAME,
                            '-M',
                            '-s', '/sbin/nologin',
                            self.config.SMB_USERNAME
                        ], check=True)
                        logging.warning(f"UID {self.nfs_uid}가 사용 중이어서 시스템이 할당한 UID로 사용자를 생성했습니다. 공유 폴더에 권한이 없을 수 있습니다.")
            
            # Samba 사용자 비밀번호 설정
            password_bytes = f"{self.config.SMB_PASSWORD}\n{self.config.SMB_PASSWORD}\n".encode('utf-8')
            subprocess.run(['sudo', 'smbpasswd', '-a', self.config.SMB_USERNAME],
                         input=password_bytes, check=True)
            
            # Samba 사용자 활성화
            subprocess.run(['sudo', 'smbpasswd', '-e', self.config.SMB_USERNAME], check=True)
            
            # 서비스 시작이 요청된 경우
            if start_service:
                subprocess.run(['sudo', 'systemctl', 'start', 'smbd'], check=True)
                subprocess.run(['sudo', 'systemctl', 'start', 'nmbd'], check=True)
            
            logging.info(f"SMB 사용자 설정이 완료되었습니다.")
        except Exception as e:
            logging.error(f"SMB 사용자의 UID/GID 설정 실패: {str(e)}")
            if start_service:
                try:
                    subprocess.run(['sudo', 'systemctl', 'start', 'smbd'], check=True)
                    subprocess.run(['sudo', 'systemctl', 'start', 'nmbd'], check=True)
                except Exception as restart_error:
                    logging.error(f"Samba 서비스 재시작 실패: {restart_error}")
            raise

    def _create_symlink(self, subfolder: str) -> bool:
        """특정 폴더의 심볼릭 링크 생성"""
        try:
            source_path = os.path.join(self.config.MOUNT_PATH, subfolder)
            link_path = os.path.join(self.links_dir, subfolder.replace(os.sep, '_'))
            
            # 이미 존재하는 링크 제거
            if os.path.exists(link_path):
                os.remove(link_path)
            
            # 심볼릭 링크 생성
            os.symlink(source_path, link_path)
            
            self.active_links.add(subfolder)
            logging.info(f"심볼릭 링크 생성됨: {link_path} -> {source_path}")
            return True
        except Exception as e:
            logging.error(f"심볼릭 링크 생성 실패 ({subfolder}): {e}")
            return False

    def _remove_symlink(self, subfolder: str) -> bool:
        """특정 폴더의 심볼릭 링크 제거"""
        try:
            link_path = os.path.join(self.links_dir, subfolder.replace(os.sep, '_'))
            if os.path.exists(link_path):
                os.remove(link_path)
                self.active_links.discard(subfolder)
                logging.info(f"심볼릭 링크 제거됨: {link_path}")
            return True
        except Exception as e:
            logging.error(f"심볼릭 링크 제거 실패 ({subfolder}): {e}")
            return False

    def _create_links_for_recently_modified(self) -> None:
        """마지막 VM 시작 시간 이후에 수정된 폴더들의 링크 생성"""
        try:
            recently_modified = []
            for path, mtime in self.previous_mtimes.items():
                if mtime > self.last_vm_start_time:
                    recently_modified.append(path)
                    last_modified = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
                    logging.info(f"최근 수정된 폴더 감지 ({path}): {last_modified}")
            
            if recently_modified:
                logging.info(f"마지막 VM 시작({datetime.fromtimestamp(self.last_vm_start_time).strftime('%Y-%m-%d %H:%M:%S')}) 이후 수정된 폴더 {len(recently_modified)}개의 링크를 생성합니다.")
                for folder in recently_modified:
                    if self._create_symlink(folder):
                        logging.debug(f"초기 링크 생성 성공: {folder}")
                    else:
                        logging.error(f"초기 링크 생성 실패: {folder}")
                
                # SMB 공유 활성화
                self._activate_smb_share()

                # VM이 정지 상태이고 최근 수정된 파일이 있는 경우 VM 시작
                if not self.proxmox_api.is_vm_running():
                    logging.info("최근 수정된 폴더가 있어 VM을 시작합니다.")
                    if self.proxmox_api.start_vm():
                        logging.info("VM 시작 성공")
                    else:
                        logging.error("VM 시작 실패")
        except Exception as e:
            logging.error(f"최근 수정된 폴더 링크 생성 중 오류 발생: {e}")

    def _activate_smb_share(self) -> bool:
        """links_dir의 SMB 공유를 활성화"""
        try:
            # Samba 서비스 시작
            try:
                # smbd 상태 확인
                smbd_status = subprocess.run(['systemctl', 'is-active', 'smbd'], capture_output=True, text=True).stdout.strip()
                nmbd_status = subprocess.run(['systemctl', 'is-active', 'nmbd'], capture_output=True, text=True).stdout.strip()
                
                if smbd_status == 'active' or nmbd_status == 'active':
                    # 서비스가 실행 중이면 재시작
                    logging.debug("Samba 서비스가 실행 중입니다. 재시작합니다.")
                    subprocess.run(['sudo', 'systemctl', 'restart', 'smbd'], check=True)
                    subprocess.run(['sudo', 'systemctl', 'restart', 'nmbd'], check=True)
                else:
                    # 서비스가 중지되어 있으면 시작
                    logging.debug("Samba 서비스를 시작합니다.")
                    subprocess.run(['sudo', 'systemctl', 'start', 'smbd'], check=True)
                    subprocess.run(['sudo', 'systemctl', 'start', 'nmbd'], check=True)
            except subprocess.CalledProcessError as e:
                logging.error(f"Samba 서비스 제어 중 오류 발생: {e}")
                raise
            
            logging.info(f"SMB 공유 활성화 성공: {self.config.SMB_COMMENT}")
            return True
        except Exception as e:
            logging.error(f"SMB 공유 활성화 실패: {e}")
            return False

    def _get_nfs_ownership(self) -> tuple[int, int]:
        """NFS 마운트 경로의 UID/GID를 반환"""
        try:
            # ls -n 명령어로 마운트 경로의 첫 번째 항목의 UID/GID 확인
            result = subprocess.run(['ls', '-n', self.config.MOUNT_PATH], capture_output=True, text=True, check=True)
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
                    return uid, gid
            raise Exception("마운트 경로에서 파일/디렉토리를 찾을 수 없습니다.")
        except Exception as e:
            logging.error(f"NFS 마운트 경로의 소유자 정보 확인 실패: {e}")
            return 0, 0

    def _deactivate_smb_share(self) -> bool:
        """모든 SMB 공유와 심볼릭 링크를 비활성화"""
        try:
            # 모든 심볼릭 링크 제거
            active_links = list(self.active_links)  # 복사본 생성
            for folder in active_links:
                self._remove_symlink(folder)
            
            # SMB 설정에서 공유 제거
            share_name = self.config.SMB_SHARE_NAME
            
            # 설정 파일 읽기
            with open('/etc/samba/smb.conf', 'r') as f:
                lines = f.readlines()

            # [global] 섹션만 유지
            new_lines = []
            for line in lines:
                if line.strip().startswith('[') and not line.strip() == '[global]':
                    break
                new_lines.append(line)
            
            # 설정 파일 저장
            with open('/etc/samba/smb.conf', 'w') as f:
                f.writelines(new_lines)
            
            # Samba 서비스 중지
            subprocess.run(['sudo', 'systemctl', 'stop', 'smbd'], check=True)
            subprocess.run(['sudo', 'systemctl', 'stop', 'nmbd'], check=True)
            
            logging.info(f"SMB 공유 비활성화 성공")
            return True
        except Exception as e:
            logging.error(f"SMB 공유 비활성화 실패: {e}")
        return False

    def cleanup_resources(self):
        try:
            # Samba 서비스 중지
            subprocess.run(['sudo', 'systemctl', 'stop', 'smbd'], check=True, timeout=30)
            subprocess.run(['sudo', 'systemctl', 'stop', 'nmbd'], check=True, timeout=30)
            
            # 심볼릭 링크 제거
            if os.path.exists(self.links_dir):
                for filename in os.listdir(self.links_dir):
                    file_path = os.path.join(self.links_dir, filename)
                    if os.path.islink(file_path):
                        os.remove(file_path)
                    
            logging.info("리소스 정리 완료")
        except Exception as e:
            logging.error(f"리소스 정리 중 오류 발생: {e}")

    def _check_smb_status(self) -> bool:
        """SMB 서비스의 실행 상태를 확인"""
        try:
            smbd_status = subprocess.run(['systemctl', 'is-active', 'smbd'], capture_output=True, text=True).stdout.strip()
            nmbd_status = subprocess.run(['systemctl', 'is-active', 'nmbd'], capture_output=True, text=True).stdout.strip()
            return smbd_status == 'active' and nmbd_status == 'active'
        except Exception as e:
            logging.error(f"SMB 서비스 상태 확인 실패: {e}")
            return False

class GShareManager:
    def __init__(self, config: Config, proxmox_api: ProxmoxAPI):
        self.config = config
        self.proxmox_api = proxmox_api
        self.low_cpu_count = 0
        self.last_action = "프로그램 시작"
        self.last_shutdown_time = "-"
        self.folder_monitor = FolderMonitor(config, proxmox_api)
        self.proxmox_api.set_folder_monitor(self.folder_monitor)  # FolderMonitor 인스턴스 설정
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
            vm_running = self.proxmox_api.is_vm_running()
            cpu_usage = self.proxmox_api.get_cpu_usage() or 0.0
            uptime = self.proxmox_api.get_vm_uptime()
            uptime_str = self._format_uptime(uptime) if uptime is not None else "알 수 없음"

            # VM 마지막 시작 시간을 가져옵니다
            last_vm_start = datetime.fromtimestamp(self.folder_monitor.last_vm_start_time, 
                                                 pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')

            current_state = State(
                last_check_time=current_time,
                vm_running=vm_running,
                cpu_usage=round(cpu_usage, 2),
                last_action=self.last_action,
                low_cpu_count=self.low_cpu_count,
                uptime=uptime_str,
                last_shutdown_time=self.last_shutdown_time,
                monitored_folders=self.folder_monitor.get_monitored_folders(),
                last_vm_start_time=last_vm_start,
                smb_running=self.folder_monitor._check_smb_status()
            )
            logging.debug(f"상태 업데이트: {current_state.to_dict()}")
        except Exception as e:
            logging.error(f"상태 업데이트 실패: {e}")

    def monitor(self) -> None:
        last_vm_status = None  # VM 상태 변화 감지를 위한 변수
        
        while True:
            try:
                update_log_level()
                logging.debug("모니터링 루프 시작")
                
                # VM 상태 확인
                current_vm_status = self.proxmox_api.is_vm_running()
                
                
                # VM 상태가 변경되었고, 현재 종료 상태인 경우
                if last_vm_status is not None and last_vm_status != current_vm_status and not current_vm_status:
                    logging.info("VM이 종료되어 SMB 공유를 비활성화합니다.")
                    self.folder_monitor._deactivate_smb_share()
                
                last_vm_status = current_vm_status
                
                try:
                    logging.debug("폴더 수정 시간 변화 확인 중")
                    changed_folders, should_start_vm = self.folder_monitor.check_modifications()
                    if changed_folders:
                        # 변경된 폴더들의 SMB 공유 활성화
                        if self.folder_monitor._activate_smb_share():
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
    
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    formatter.converter = lambda *args: datetime.now(tz=pytz.timezone(Config().TIMEZONE)).timetuple()

    # 로그 로테이션 설정
    file_handler = RotatingFileHandler(
        'gshare_manager.log',
        maxBytes=5*1024*1024,  # 5MB
        backupCount=1,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level))

    # 기존 핸들러 제거
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    
    return logger

def update_log_level():
    """로그 레벨을 동적으로 업데이트"""
    load_dotenv()
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    logging.getLogger().setLevel(getattr(logging, log_level))

def get_default_state() -> State:
    """State가 초기화되지 않았을 때 사용할 기본값을 반환"""
    current_time = datetime.now(pytz.timezone(Config().TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
    return State(
        last_check_time=current_time,
        vm_running=False,
        cpu_usage=0.0,
        last_action="초기화되지 않음",
        low_cpu_count=0,
        uptime="알 수 없음",
        last_shutdown_time="-",
        monitored_folders={},
        last_vm_start_time="-",
        smb_running=False
    )

@app.route('/')
def show_log():
    log_content = ""
    if os.path.exists('gshare_manager.log'):
        with open('gshare_manager.log', 'r') as file:
            log_content = file.read()
    else:
        log_content = "로그 파일을 찾을 수 없습니다."

    state = current_state if current_state is not None else get_default_state()
    return render_template('index.html', 
                         state=state.to_dict(), 
                         log_content=log_content)

@app.route('/update_state')
def update_state():
    state = current_state if current_state is not None else get_default_state()
    return jsonify(state.to_dict())

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

        if current_state.vm_running:
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

        if not current_state.vm_running:
            return jsonify({"status": "error", "message": "VM이 이미 종료되어 있습니다."}), 400
        
        response = requests.post(config.SHUTDOWN_WEBHOOK_URL, timeout=5)
        response.raise_for_status()
        return jsonify({"status": "success", "message": "VM 종료가 요청되었습니다."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"VM 종료 요청 실패: {str(e)}"}), 500

@app.route('/toggle_mount/<path:folder>')
def toggle_mount(folder):
    try:
        if current_state is None:
            return jsonify({"status": "error", "message": "State not initialized."}), 404

        if folder in gshare_manager.folder_monitor.active_links:
            # 마운트 해제
            if gshare_manager.folder_monitor._remove_symlink(folder):
                # 상태 즉시 업데이트
                gshare_manager._update_state()
                return jsonify({"status": "success", "message": f"{folder} 마운트가 해제되었습니다."})
            else:
                return jsonify({"status": "error", "message": f"{folder} 마운트 해제 실패"}), 500
        else:
            # 마운트
            if gshare_manager.folder_monitor._create_symlink(folder) and gshare_manager.folder_monitor._activate_smb_share():
                # 상태 즉시 업데이트
                gshare_manager._update_state()
                return jsonify({"status": "success", "message": f"{folder} 마운트가 활성화되었습니다."})
            else:
                return jsonify({"status": "error", "message": f"{folder} 마운트 활성화 실패"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"마운트 상태 변경 실패: {str(e)}"}), 500

def run_flask_app():
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if __name__ == '__main__':
    logger = setup_logging()
    config = Config()
    
    # Flask 스레드 먼저 시작
    flask_thread = threading.Thread(target=run_flask_app)
    flask_thread.daemon = True
    flask_thread.start()
    
    try:
        logging.info("───────────────────────────────────────────────")
        proxmox_api = ProxmoxAPI(config)
        gshare_manager = GShareManager(config, proxmox_api)
        logging.info(f"VM is_running - {gshare_manager.proxmox_api.is_vm_running()}")
        logging.info("GShare 관리 시작")
        
        gshare_manager.monitor()
    except KeyboardInterrupt:
        logging.info("프로그램 종료")
        logging.info("───────────────────────────────────────────────")
    except Exception as e:
        logging.error(f"예상치 못한 오류 발생: {e}")
        logging.info("───────────────────────────────────────────────")
        # 오류가 발생해도 Flask 서버는 계속 실행
        while True:
            time.sleep(60)  # 1분마다 체크

# 프로그램 종료 시 호출
atexit.register(lambda: gshare_manager.folder_monitor.cleanup_resources() if gshare_manager else None)
