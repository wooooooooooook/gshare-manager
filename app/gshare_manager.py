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
import threading
import sys
import atexit
from web_server import init_server, run_flask_app, run_landing_page
import urllib3
import yaml

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
    threshold_count: int
    uptime: str
    last_shutdown_time: str
    monitored_folders: dict
    smb_running: bool
    check_interval: int
    
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
                            
            return True
        except Exception as e:
            return False

class FolderMonitor:
    def __init__(self, config: Config, proxmox_api: ProxmoxAPI):
        self.config = config
        self.proxmox_api = proxmox_api
        self.previous_mtimes = {}  # 각 서브폴더별 이전 수정 시간을 저장
        self.active_links = set()  # 현재 활성화된 심볼릭 링크 목록
        self.last_shutdown_time = 0  # 초기화
        
        try:
            self.last_shutdown_time = self._load_last_shutdown_time()  # VM 마지막 종료 시간
        except Exception as e:
            logging.error(f"마지막 종료 시간 로드 실패: {e}")
            self.last_shutdown_time = time.time()  # 현재 시간으로 설정
        
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
        
        # NFS 마운트 경로의 UID/GID 확인 (중요: 이 작업은 실패하면 안 됨)
        logging.info("NFS 마운트 경로의 UID/GID 확인 중...")
        self.nfs_uid, self.nfs_gid = self._get_nfs_ownership()
        logging.info(f"NFS 마운트 경로의 UID/GID 확인 완료: {self.nfs_uid}/{self.nfs_gid}")
        
        # SMB 사용자의 UID/GID 설정 (서비스는 시작하지 않음)
        self._set_smb_user_ownership(start_service=False)
        
        # 초기 실행 시 마지막 VM 시작 시간 이후에 수정된 폴더들의 링크 생성
        self._create_links_for_recently_modified()

    def _load_last_shutdown_time(self) -> float:
        """VM 마지막 종료 시간을 로드 (UTC 기준)"""
        try:
            if os.path.exists('last_shutdown.txt'):
                with open('last_shutdown.txt', 'r') as f:
                    return float(f.read().strip())
            else:
                # 파일이 없는 경우 현재 UTC 시간을 저장하고 반환
                current_time = datetime.now(pytz.UTC).timestamp()
                with open('last_shutdown.txt', 'w') as f:
                    f.write(str(current_time))
                logging.info(f"VM 종료 시간 파일이 없어 현재 시간으로 생성: {datetime.fromtimestamp(current_time, pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')}")
                return current_time
        except Exception as e:
            # 오류 발생 시 현재 UTC 시간 사용
            current_time = datetime.now(pytz.UTC).timestamp()
            logging.error(f"VM 마지막 종료 시간 로드 실패: {e}, 현재 시간을 사용합니다.")
            return current_time

    def _save_last_shutdown_time(self) -> None:
        """현재 시간을 VM 마지막 종료 시간으로 저장 (UTC 기준)"""
        try:
            # UTC 기준 현재 시간
            current_time = datetime.now(pytz.UTC).timestamp()
            with open('last_shutdown.txt', 'w') as f:
                f.write(str(current_time))
            self.last_shutdown_time = current_time
            logging.info(f"VM 종료 시간 저장됨: {datetime.fromtimestamp(current_time, pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            logging.error(f"VM 종료 시간 저장 실패: {e}")

    def _ensure_smb_installed(self):
        """Samba가 설치되어 있는지 확인하고 설치"""
        try:
            subprocess.run(['which', 'smbd'], check=True, capture_output=True)
            logging.info("Samba가 이미 설치되어 있습니다.")
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
        """지정된 경로의 폴더 수정 시간을 반환 (UTC 기준)"""
        try:
            full_path = os.path.join(self.config.MOUNT_PATH, path)
            return os.path.getmtime(full_path)
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
                last_modified = datetime.fromtimestamp(current_mtime, pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
                logging.info(f"폴더 수정 시간 변화 감지 ({path}): {last_modified}")
                changed_folders.append(path)
                self.previous_mtimes[path] = current_mtime
                
                # 심볼릭 링크 생성
                self._create_symlink(path)
                
                # VM 마지막 시작 시간보다 수정 시간이 더 최근인 경우
                if current_mtime > self.last_shutdown_time:
                    should_start_vm = True
                    logging.info(f"VM 시작 조건 충족 - 수정 시간: {last_modified}")
        
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

    def _ensure_links_directory(self):
        """공유용 링크 디렉토리 생성"""
        try:
            if not os.path.exists(self.links_dir):
                os.makedirs(self.links_dir)
                logging.info(f"공유용 링크 디렉토리 생성됨: {self.links_dir}")
            
            # 디렉토리 권한 설정
            os.chmod(self.links_dir, 0o755)
            
            try:
                subprocess.run(['sudo', 'chown', f"{self.config.SMB_USERNAME}:{self.config.SMB_USERNAME}", self.links_dir], check=True)
                logging.info("공유용 링크 디렉토리 권한 설정 완료")
            except subprocess.CalledProcessError as e:
                logging.error(f"디렉토리 소유권 변경 실패: {e} - 사용자가 시스템에 존재하는지 확인하세요.")
                # 사용자 생성 로직 호출
                self._set_smb_user_ownership(start_service=False)
                # 다시 시도
                subprocess.run(['sudo', 'chown', f"{self.config.SMB_USERNAME}:{self.config.SMB_USERNAME}", self.links_dir], check=True)
                logging.info("사용자 생성 후 공유용 링크 디렉토리 권한 설정 완료")
        except Exception as e:
            logging.error(f"공유용 링크 디렉토리 생성/설정 실패: {e}")
            raise

    def _set_smb_user_ownership(self, start_service: bool = True) -> None:
        """SMB 사용자의 UID/GID를 NFS 마운트 경로와 동일하게 설정"""
        try:
            # 설정에서 SMB 사용자 이름 가져오기
            smb_username = self.config.SMB_USERNAME
            logging.info(f"SMB 사용자 설정: {smb_username}")
            
            # 현재 SMB 사용자의 존재 여부 확인
            user_exists = False
            try:
                user_info = subprocess.run(['id', smb_username], capture_output=True, text=True)
                user_exists = user_info.returncode == 0
                logging.info(f"사용자 존재 여부: {user_exists}")
            except Exception as e:
                logging.error(f"사용자 확인 중 오류: {e}")
                user_exists = False
                
            # 사용자가 없으면 생성
            if not user_exists:
                logging.info(f"사용자 '{smb_username}'가 존재하지 않습니다. 사용자를 생성합니다.")
                try:
                    # 사용자 그룹 생성 시도
                    group_result = subprocess.run(['groupadd', '-r', smb_username], capture_output=True, text=True)
                    logging.info(f"그룹 생성 결과: {group_result.returncode}, 출력: {group_result.stdout}, 오류: {group_result.stderr}")
                    
                    # 사용자 생성 시도
                    user_result = subprocess.run(['useradd', '-r', '-g', smb_username, smb_username], capture_output=True, text=True)
                    logging.info(f"사용자 생성 결과: {user_result.returncode}, 출력: {user_result.stdout}, 오류: {user_result.stderr}")
                    
                    # SMB 패스워드 설정은 생략 (디버깅 중)
                    logging.info(f"사용자 '{smb_username}' 생성 완료")
                except Exception as e:
                    logging.error(f"사용자 생성 중 오류 발생: {e}")
            
            # 현재 SMB 사용자의 UID/GID 확인 (사용자가 생성된 후)
            try:
                user_info = subprocess.run(['id', smb_username], capture_output=True, text=True)
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
                if mtime > self.last_shutdown_time:
                    recently_modified.append(path)
                    last_modified = datetime.fromtimestamp(mtime, pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
                    logging.info(f"최근 수정된 폴더 감지 ({path}): {last_modified}")
            
            if recently_modified:
                logging.info(f"마지막 VM 종료({datetime.fromtimestamp(self.last_shutdown_time, pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')}) 이후 수정된 폴더 {len(recently_modified)}개의 링크를 생성합니다.")
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
            # 먼저 SMB 사용자의 UID/GID 설정을 업데이트
            self._set_smb_user_ownership(start_service=True)
            
            share_name = self.config.SMB_SHARE_NAME
            
            # 공유 설정 생성
            share_config = f"""
[{share_name}]
   path = {self.links_dir}
   comment = {self.config.SMB_COMMENT}
   browseable = yes
   guest ok = {'yes' if self.config.SMB_GUEST_OK else 'no'}
   read only = yes
   create mask = 0644
   directory mask = 0755
   force user = {self.config.SMB_USERNAME}
   force group = {self.config.SMB_USERNAME}
   veto files = /@*
   hide dot files = yes
   delete veto files = no
   follow symlinks = yes
   wide links = yes
   unix extensions = no
"""
            # 설정 파일 읽기
            with open('/etc/samba/smb.conf', 'r') as f:
                lines = f.readlines()

            # [global] 섹션만 유지
            new_lines = []
            for line in lines:
                if line.strip().startswith('[') and not line.strip() == '[global]':
                    break
                new_lines.append(line)
            
            # 새로운 공유 설정 추가
            new_lines.append(share_config)
            
            # 설정 파일 저장
            with open('/etc/samba/smb.conf', 'w') as f:
                f.writelines(new_lines)
            
            # Samba 서비스 상태 확인 및 시작/재시작

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
        
        try:
            # 마운트 경로의 소유자 정보 직접 확인 (stat 사용)
            stat_info = os.stat(self.config.MOUNT_PATH)
            uid = stat_info.st_uid
            gid = stat_info.st_gid
            logging.info(f"NFS 마운트 경로의 UID/GID: {uid}/{gid} (소유자: {uid}, 그룹: {gid})")
            return uid, gid
        except Exception as e:
            # ls -n 명령어로 마운트 경로의 첫 번째 항목의 UID/GID 확인 (대체 방법)
            try:
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
                        logging.info(f"NFS 마운트 경로의 UID/GID: {uid}/{gid} (ls -n 명령으로 확인)")
                        return uid, gid
                
                # 디렉토리가 비어있는 경우
                error_msg = "마운트 경로가 비어있거나 파일/디렉토리 정보를 읽을 수 없습니다"
                logging.error(error_msg)
                raise FileNotFoundError(error_msg)
            except subprocess.CalledProcessError as e:
                error_msg = f"ls 명령 실행 실패: {e}"
                logging.error(error_msg)
                raise RuntimeError(error_msg)
            
        # 이 코드에 도달하면 오류가 발생한 것
        error_msg = "NFS 마운트 경로의 UID/GID를 확인할 수 없습니다"
        logging.error(error_msg)
        raise RuntimeError(error_msg)

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
        self.folder_monitor = FolderMonitor(config, proxmox_api)
        self.proxmox_api.set_folder_monitor(self.folder_monitor)  # FolderMonitor 인스턴스 설정
        self.last_shutdown_time = datetime.fromtimestamp(self.folder_monitor.last_shutdown_time).strftime('%Y-%m-%d %H:%M:%S')
        self.restart_required = False
        
        # NFS 마운트 시도
        self._mount_nfs()
    
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
                logging.info(f"마운트 디렉토리 생성: {mount_path}")
            
            # 이미 마운트되어 있는지 확인
            mount_check = subprocess.run(['mount', '-t', 'nfs'], capture_output=True, text=True)
            if nfs_path in mount_check.stdout and mount_path in mount_check.stdout:
                logging.info(f"NFS가 이미 마운트되어 있습니다: {nfs_path} -> {mount_path}")
                return
                
            # NFS 마운트 시도
            logging.info(f"NFS 마운트 시도: {nfs_path} -> {mount_path}")
            mount_cmd = ['mount', '-t', 'nfs', '-o', 'nolock,vers=3,soft,timeo=100', nfs_path, mount_path]
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
                response = requests.post(self.config.SHUTDOWN_WEBHOOK_URL, timeout=5)
                response.raise_for_status()

                uptime = self.proxmox_api.get_vm_uptime()
                uptime_str = self._format_uptime(uptime) if uptime is not None else "알 수 없음"
                self.last_shutdown_time = datetime.now(pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
                
                # VM 종료 시간 저장
                self.folder_monitor._save_last_shutdown_time()
                
                # VM 종료 시 모든 SMB 공유 비활성화
                self.folder_monitor._deactivate_smb_share()
                
                logging.info(f"종료 웹훅 전송 성공, 업타임: {uptime_str}")
            except Exception as e:
                logging.error(f"종료 웹훅 전송 실패: {e}")
        else:
            logging.info("종료 웹훅을 전송하려했지만 vm이 이미 종료상태입니다.")

    def _update_state(self) -> State:
        try:
            global current_state
            current_time = datetime.now(pytz.timezone(self.config.TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S')
            vm_running = self.proxmox_api.is_vm_running()
            cpu_usage = self.proxmox_api.get_cpu_usage() or 0.0
            uptime = self.proxmox_api.get_vm_uptime()
            uptime_str = self._format_uptime(uptime) if uptime is not None else "알 수 없음"
            # 타임존을 고려하여 timestamp를 문자열로 변환
            self.last_shutdown_time = datetime.fromtimestamp(
                self.folder_monitor.last_shutdown_time,
                pytz.timezone(self.config.TIMEZONE)
            ).strftime('%Y-%m-%d %H:%M:%S')

            current_state = State(
                last_check_time=current_time,
                vm_running=vm_running,
                cpu_usage=round(cpu_usage, 2),
                last_action=self.last_action,
                low_cpu_count=self.low_cpu_count,
                threshold_count=self.config.THRESHOLD_COUNT,
                uptime=uptime_str,
                last_shutdown_time=self.last_shutdown_time,
                monitored_folders=self.folder_monitor.get_monitored_folders(),
                smb_running=self.folder_monitor._check_smb_status(),
                check_interval=self.config.CHECK_INTERVAL
            )
            return current_state
        except Exception as e:
            logging.error(f"상태 업데이트 실패: {e}")
            return None

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
                    # 상태 업데이트 (웹서버용)
                    global current_state
                    current_state = self._update_state()
                except Exception as e:
                    logging.error(f"상태 업데이트 중 오류: {e}")

                time.sleep(self.config.CHECK_INTERVAL)
                
            except Exception as e:
                logging.error(f"모니터링 루프에서 예상치 못한 오류 발생: {e}")
                time.sleep(self.config.CHECK_INTERVAL)  # 오류 발생시에도 대기 후 계속 실행

def setup_logging(log_file: str = 'gshare_manager.log'):
    load_dotenv()
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # 기본 시간대를 'Asia/Seoul'로 설정
    formatter.converter = lambda *args: datetime.now(tz=pytz.timezone('Asia/Seoul')).timetuple()

    # 로그 로테이션 설정
    file_handler = RotatingFileHandler(
        log_file,
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

def check_config_complete():
    """설정이 완료되었는지 확인"""
    try:
        # 모든 실행이 Docker 환경에서 이루어진다고 가정
        config_path = '/config/config.yaml'
        init_flag_path = '/config/.init_complete'
        
        # 설정 파일이 없으면 무조건 랜딩 페이지로 이동
        if not os.path.exists(config_path):
            logging.info("설정 파일이 없습니다. 랜딩 페이지로 이동합니다.")
            return False
            
        # 초기화 완료 플래그가 없으면 랜딩 페이지로 이동
        if not os.path.exists(init_flag_path):
            logging.info("초기 설정이 필요합니다. 랜딩 페이지로 이동합니다.")
            return False
            
        # 설정 파일이 존재하면 기본 설정이 있는지 확인
        with open(config_path, 'r', encoding='utf-8') as f:
            yaml_config = yaml.safe_load(f)
            
        if not yaml_config:
            logging.warning("설정 파일이 비어있습니다.")
            return False
        
        # 모든 필수 필드가 있는지 확인
        # 필수 섹션과 필드 확인
        if 'credentials' not in yaml_config:
            logging.warning("인증 정보가 설정되지 않았습니다.")
            return False
            
        # 필수 인증 정보 확인
        required_credentials = [
            'proxmox_host', 'token_id', 'secret', 
            'shutdown_webhook_url', 'smb_username', 'smb_password'
        ]
        
        # 필수 값들이 누락되었는지 확인
        missing_credentials = []
        for cred in required_credentials:
            if cred not in yaml_config['credentials'] or not yaml_config['credentials'][cred]:
                missing_credentials.append(cred)
                
        if missing_credentials:
            logging.warning(f"다음 설정이 누락되었습니다: {', '.join(missing_credentials)}")
            return False
            
        return True
    except Exception as e:
        logging.error(f"설정 확인 중 오류 발생: {e}")
        return False

if __name__ == "__main__":
    setup_logging()
    logging.info("GShare 매니저 시작")
    
    try:
        # 모든 환경을 Docker로 간주
        
        # 설정 완료 여부 확인
        setup_completed = check_config_complete()
        
        if not setup_completed:
            logging.info("랜딩 페이지 진입이 필요합니다.")
            # 랜딩 페이지 실행
            run_landing_page()
            sys.exit(0)
        
        # 설정이 완료된 경우 애플리케이션 실행
        logging.info("설정이 완료되었습니다. 애플리케이션 실행 중...")
        
        # 설정 로드
        config = Config.load_config()
        
        # 시간대 설정
        os.environ['TZ'] = config.TIMEZONE
        try:
            time.tzset()
        except AttributeError:
            # Windows에서는 tzset()을 지원하지 않음
            pass
        
        # Proxmox API 경고 비활성화
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # 로그 파일 경로 설정 (항상 Docker 환경)
        log_dir = '/logs'
        os.makedirs(log_dir, exist_ok=True)
        
        log_file = os.path.join(log_dir, 'gshare_manager.log')
        setup_logging(log_file)
        
        # 객체 초기화
        proxmox_api = ProxmoxAPI(config)
        gshare_manager = GShareManager(config, proxmox_api)
        logging.info(f"VM is_running - {gshare_manager.proxmox_api.is_vm_running()}")
        logging.info("GShare 관리 시작")
        
        # Flask 앱 초기화 및 상태 전달
        current_state = gshare_manager._update_state()
        init_server(current_state, gshare_manager, config)
        
        # Flask 스레드 시작
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
        # 오류가 발생해도 Flask 서버는 계속 실행
        while True:
            time.sleep(60)  # 1분마다 체크

# 프로그램 종료 시 호출
atexit.register(lambda: gshare_manager.folder_monitor.cleanup_resources() if gshare_manager else None)
