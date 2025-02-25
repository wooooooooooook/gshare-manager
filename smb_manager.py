import logging
import os
import subprocess
from config import Config

class SMBManager:
    def __init__(self, config: Config, links_dir: str):
        self.config = config
        self.links_dir = links_dir
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
   # SMB1
   server min protocol = NT1
   server max protocol = NT1
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

    def set_smb_user_ownership(self, nfs_uid: int, nfs_gid: int, start_service: bool = True) -> None:
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
                    if current_uid == nfs_uid and current_gid == nfs_gid:
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
                group_info = subprocess.run(['getent', 'group', str(nfs_gid)], 
                                         capture_output=True, text=True).stdout.strip()
                if group_info:
                    # GID가 이미 사용 중이면 해당 그룹 사용
                    existing_group = group_info.split(':')[0]
                    self.config.SMB_USERNAME = existing_group
                else:
                    # 그룹이 없으면 생성
                    try:
                        subprocess.run(['sudo', 'groupadd', '-g', str(nfs_gid), self.config.SMB_USERNAME], check=True)
                        logging.info(f"그룹 '{self.config.SMB_USERNAME}' 생성됨 (GID: {nfs_gid})")
                    except subprocess.CalledProcessError as e:
                        if e.returncode == 4:  # GID already exists
                            # 다른 GID로 시도
                            subprocess.run(['sudo', 'groupadd', self.config.SMB_USERNAME], check=True)
                            logging.warning(f"GID {nfs_gid}가 사용 중이어서 시스템이 할당한 GID로 그룹을 생성했습니다. 공유 폴더에 권한이 없을 수 있습니다.")
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
                        '-u', str(nfs_uid),
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
                        logging.warning(f"UID {nfs_uid}가 사용 중이어서 시스템이 할당한 UID로 사용자를 생성했습니다. 공유 폴더에 권한이 없을 수 있습니다.")
            
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

    def activate_smb_share(self, nfs_uid: int, nfs_gid: int) -> bool:
        """links_dir의 SMB 공유를 활성화"""
        try:
            # 먼저 SMB 사용자의 UID/GID 설정을 업데이트
            self.set_smb_user_ownership(nfs_uid, nfs_gid, start_service=True)
            
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

    def deactivate_smb_share(self) -> bool:
        """모든 SMB 공유를 비활성화"""
        try:
            # SMB 설정에서 공유 제거
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

    def check_smb_status(self) -> bool:
        """SMB 서비스의 실행 상태를 확인"""
        try:
            smbd_status = subprocess.run(['systemctl', 'is-active', 'smbd'], capture_output=True, text=True).stdout.strip()
            nmbd_status = subprocess.run(['systemctl', 'is-active', 'nmbd'], capture_output=True, text=True).stdout.strip()
            return smbd_status == 'active' and nmbd_status == 'active'
        except Exception as e:
            logging.error(f"SMB 서비스 상태 확인 실패: {e}")
            return False

    def cleanup_resources(self):
        """SMB 관련 리소스 정리"""
        try:
            # Samba 서비스 중지
            subprocess.run(['sudo', 'systemctl', 'stop', 'smbd'], check=True, timeout=30)
            subprocess.run(['sudo', 'systemctl', 'stop', 'nmbd'], check=True, timeout=30)
            logging.info("SMB 서비스 중지 완료")
        except Exception as e:
            logging.error(f"SMB 리소스 정리 중 오류 발생: {e}") 