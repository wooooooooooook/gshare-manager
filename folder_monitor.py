import logging
import os
import subprocess
from datetime import datetime
import pytz
import requests
from config import Config
from smb_manager import SMBManager

class FolderMonitor:
    def __init__(self, config: Config, proxmox_api):
        self.config = config
        self.proxmox_api = proxmox_api
        self.previous_mtimes = {}  # 각 서브폴더별 이전 수정 시간을 저장
        self.active_links = set()  # 현재 활성화된 심볼릭 링크 목록
        self.last_shutdown_time = self._load_last_shutdown_time()  # VM 마지막 종료 시간
        
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
        
        # SMB 관리자 초기화
        self.smb_manager = SMBManager(config, self.links_dir)
        
        # NFS 마운트 경로의 UID/GID 확인
        self.nfs_uid, self.nfs_gid = self._get_nfs_ownership()
        logging.debug(f"NFS 마운트 경로의 UID/GID: {self.nfs_uid}/{self.nfs_gid}")
        
        # SMB 사용자의 UID/GID 설정 (서비스는 시작하지 않음)
        self.smb_manager.set_smb_user_ownership(self.nfs_uid, self.nfs_gid, start_service=False)
        
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
                self.smb_manager.activate_smb_share(self.nfs_uid, self.nfs_gid)

                # VM이 정지 상태이고 최근 수정된 파일이 있는 경우 VM 시작
                if not self.proxmox_api.is_vm_running():
                    logging.info("최근 수정된 폴더가 있어 VM을 시작합니다.")
                    if self.proxmox_api.start_vm():
                        logging.info("VM 시작 성공")
                    else:
                        logging.error("VM 시작 실패")
        except Exception as e:
            logging.error(f"최근 수정된 폴더 링크 생성 중 오류 발생: {e}")

    def send_shutdown_webhook(self) -> bool:
        """VM 종료 웹훅 전송"""
        if self.proxmox_api.is_vm_running():
            try:
                response = requests.post(self.config.SHUTDOWN_WEBHOOK_URL, timeout=5)
                response.raise_for_status()

                # VM 종료 시간 저장
                self._save_last_shutdown_time()
                
                # VM 종료 시 모든 SMB 공유 비활성화
                self.smb_manager.deactivate_smb_share()
                
                logging.info(f"종료 웹훅 전송 성공")
                return True
            except Exception as e:
                logging.error(f"종료 웹훅 전송 실패: {e}")
                return False
        else:
            logging.info("종료 웹훅을 전송하려했지만 vm이 이미 종료상태입니다.")
            return False

    def cleanup_resources(self):
        """리소스 정리"""
        try:
            # SMB 리소스 정리
            self.smb_manager.cleanup_resources()
            
            # 심볼릭 링크 제거
            if os.path.exists(self.links_dir):
                for filename in os.listdir(self.links_dir):
                    file_path = os.path.join(self.links_dir, filename)
                    if os.path.islink(file_path):
                        os.remove(file_path)
                    
            logging.info("리소스 정리 완료")
        except Exception as e:
            logging.error(f"리소스 정리 중 오류 발생: {e}")

    def check_smb_status(self) -> bool:
        """SMB 서비스의 실행 상태를 확인"""
        return self.smb_manager.check_smb_status() 