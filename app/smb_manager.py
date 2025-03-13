import logging
import os
import subprocess
import time
from config import GshareConfig  # type: ignore


class SMBManager:
    """SMB 서비스 관리 클래스"""

    def __init__(self, config: GshareConfig, nfs_uid: int, nfs_gid: int):
        """
        SMB 관리자 초기화

        Args:
            config: 설정 객체
            links_dir: 공유용 링크 디렉토리 경로
            nfs_uid: NFS 마운트 경로의 UID
            nfs_gid: NFS 마운트 경로의 GID
        """
        self.config = config
        self.links_dir = self.config.SMB_LINKS_DIR
        self.nfs_uid = nfs_uid
        self.nfs_gid = nfs_gid

        # 초기화 작업
        self._init_smb_config()
        self._set_smb_user_ownership(start_service=False)

        # 공유용 링크 디렉토리 생성 및 권한 설정
        self.set_links_directory_permissions(self.links_dir)

        # 시작 시 기존 심볼릭 링크 모두 제거
        self.cleanup_all_symlinks()

    def _init_smb_config(self) -> None:
        """기본 SMB 설정 초기화"""
        try:
            # smb.conf 파일 백업
            if not os.path.exists('/etc/samba/smb.conf.backup'):
                subprocess.run(['cp', '/etc/samba/smb.conf',
                               '/etc/samba/smb.conf.backup'], check=True)

            # 기본 설정 생성
            base_config = """[global]
   workgroup = WORKGROUP
   server string = Samba Server
   server role = standalone server
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no
   # 포트 설정
   smb ports = {}
   # SMB1 설정
   server min protocol = NT1
   server max protocol = NT1
   # 심볼릭 링크 설정
   follow symlinks = yes
   wide links = yes
   unix extensions = no
   allow insecure wide links = yes
   # 보안 설정
   ntlm auth = yes
   client ntlmv2 auth = no
   lanman auth = yes
""".format(self.config.SMB_PORT)
            # 기본 설정 저장
            with open('/etc/samba/smb.conf', 'w') as f:
                f.write(base_config)

            # Samba 사용자 추가
            try:
                subprocess.run(['smbpasswd', '-a', self.config.SMB_USERNAME],
                               input=f"{self.config.SMB_PASSWORD}\n{self.config.SMB_PASSWORD}\n".encode(
                ),
                    capture_output=True)
            except subprocess.CalledProcessError:
                pass  # 사용자가 이미 존재하는 경우 무시

            logging.debug("SMB 기본 설정 초기화 완료")
        except Exception as e:
            logging.error(f"SMB 기본 설정 초기화 실패: {e}")
            raise

    def check_smb_status(self) -> bool:
        """SMB 서비스가 실행 중인지 확인"""
        try:
            smbd_running = subprocess.run(
                ['pgrep', 'smbd'], capture_output=True).returncode == 0
            nmbd_running = subprocess.run(
                ['pgrep', 'nmbd'], capture_output=True).returncode == 0
            return smbd_running and nmbd_running
        except Exception as e:
            logging.error(f"SMB 상태 확인 중 오류: {e}")
            return False

    def start_samba_service(self) -> None:
        """Samba 서비스 시작"""
        try:
            # Docker 환경에서는 직접 데몬 실행
            subprocess.run(['smbd', '--daemon'], check=False)
            subprocess.run(['nmbd', '--daemon'], check=False)
        except Exception as e:
            logging.error(f"Samba 서비스 시작 실패: {e}")
            raise

    def stop_samba_service(self, timeout=None) -> None:
        """Samba 서비스 중지"""
        try:
            # 프로세스 종료
            subprocess.run(['pkill', 'smbd'], check=False)
            subprocess.run(['pkill', 'nmbd'], check=False)
        except Exception as e:
            logging.error(f"Samba 서비스 중지 실패: {e}")

    def restart_samba_service(self) -> None:
        """Samba 서비스 재시작"""
        try:
            self.stop_samba_service()
            time.sleep(1)  # 잠시 대기
            self.start_samba_service()
        except Exception as e:
            logging.error(f"Samba 서비스 재시작 실패: {e}")
            raise

    def update_smb_config(self) -> None:
        """SMB 설정 파일 업데이트"""
        try:
            # 먼저 SMB 사용자의 UID/GID 설정을 업데이트
            self._set_smb_user_ownership(start_service=False)

            share_name = self.config.SMB_SHARE_NAME

            # 설정 파일 읽기
            with open('/etc/samba/smb.conf', 'r') as f:
                lines = f.readlines()

            # [global] 섹션을 찾아서 포트 설정 추가/업데이트
            global_section_found = False
            port_set = False
            new_lines = []

            for line in lines:
                # [global] 섹션 감지
                if line.strip() == '[global]':
                    global_section_found = True
                    new_lines.append(line)
                # 다른 섹션 시작 감지
                elif line.strip().startswith('[') and line.strip() != '[global]':
                    global_section_found = False
                    # 포트 설정이 없었다면 여기서 추가
                    if not port_set:
                        new_lines.append(
                            f"   smb ports = {self.config.SMB_PORT}\n")
                        port_set = True
                    new_lines.append(line)
                # global 섹션 내에서 포트 설정 업데이트
                elif global_section_found and "smb ports" in line.lower():
                    new_lines.append(
                        f"   smb ports = {self.config.SMB_PORT}\n")
                    port_set = True
                else:
                    new_lines.append(line)

            # [global] 섹션만 있고 다른 섹션이 없는 경우를 처리
            if global_section_found and not port_set:
                new_lines.append(f"   smb ports = {self.config.SMB_PORT}\n")

            # 여기부터는 기존 코드 - 여기까지의 코드로 [global] 섹션까지만 유지하고 포트 설정도 업데이트
            final_lines = []
            for line in new_lines:
                if line.strip().startswith('[') and not line.strip() == '[global]':
                    break
                final_lines.append(line)

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
   allow insecure wide links = yes
   ntlm auth = yes
   lanman auth = yes
"""
            # 새로운 공유 설정 추가
            final_lines.append(share_config)

            # 설정 파일 저장
            with open('/etc/samba/smb.conf', 'w') as f:
                f.writelines(final_lines)

            logging.debug(
                f"SMB 설정 파일이 업데이트 되었습니다: {share_name}, 포트: {self.config.SMB_PORT}")
        except Exception as e:
            logging.error(f"SMB 설정 파일 업데이트 실패: {e}")
            raise

    def activate_smb_share(self) -> bool:
        """SMB 공유 활성화 - SMB 설정 파일 업데이트 및 서비스 재시작"""
        try:
            # 이미 SMB 서비스가 실행 중인지 확인
            is_running = self.check_smb_status()

            # SMB 설정 파일 업데이트
            self.update_smb_config()

            # 서비스 재시작/시작
            if is_running:
                logging.debug("SMB 서비스가 이미 실행 중입니다. 서비스를 재시작합니다.")
                self.restart_samba_service()
            else:
                logging.debug("SMB 서비스를 시작합니다.")
                self.start_samba_service()

            # 서비스 상태 확인
            if self.check_smb_status():
                logging.debug("SMB 공유가 활성화되었습니다.")
                return True
            else:
                logging.error("SMB 서비스가 시작되지 않았습니다.")
                return False

        except Exception as e:
            logging.error(f"SMB 공유 활성화 실패: {e}")
            return False

    def deactivate_smb_share(self) -> bool:
        """모든 SMB 공유 비활성화"""
        try:
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
            self.stop_samba_service()

            logging.debug(f"SMB 공유 비활성화 성공")
            return True
        except Exception as e:
            logging.error(f"SMB 공유 비활성화 실패: {e}")
        return False

    def _set_smb_user_ownership(self, start_service: bool = True) -> None:
        """SMB 사용자의 UID/GID를 NFS 마운트 경로와 동일하게 설정"""
        try:
            # 설정에서 SMB 사용자 이름과 비밀번호 가져오기
            smb_username = self.config.SMB_USERNAME
            smb_password = self.config.SMB_PASSWORD
            logging.debug(f"SMB 사용자 설정: {smb_username}")

            # 사용자 존재 여부와 UID/GID 일치 여부 한 번에 확인
            user_exists = False
            uid_gid_match = False
            current_uid = 0
            current_gid = 0

            try:
                user_info = subprocess.run(
                    ['id', smb_username], capture_output=True, text=True)
                if user_info.returncode == 0:  # 사용자 존재
                    user_exists = True
                    info_parts = user_info.stdout.strip().split()
                    current_uid = int(
                        info_parts[0].split('=')[1].split('(')[0])
                    current_gid = int(
                        info_parts[1].split('=')[1].split('(')[0])
                    uid_gid_match = (
                        current_uid == self.nfs_uid and current_gid == self.nfs_gid)
                    logging.debug(
                        f"사용자 '{smb_username}' 존재: UID={current_uid}, GID={current_gid}")
                    logging.debug(
                        f"NFS UID/GID와 일치: {uid_gid_match} (NFS: UID={self.nfs_uid}, GID={self.nfs_gid})")
            except Exception as e:
                logging.error(f"사용자 확인 중 오류: {e}")

            # NFS GID가 이미 존재하는 그룹에 할당되어 있는지 확인
            existing_group = None
            try:
                group_info = subprocess.run(['getent', 'group', str(
                    self.nfs_gid)], capture_output=True, text=True)
                if group_info.returncode == 0:  # 해당 GID를 가진 그룹이 존재
                    existing_group = group_info.stdout.strip().split(':')[0]
                    logging.debug(
                        f"GID {self.nfs_gid}를 가진 기존 그룹 발견: {existing_group}")
            except Exception as e:
                logging.error(f"그룹 확인 중 오류: {e}")

            # 사용자 처리 로직
            if not user_exists:
                # 1. 사용자가 없음 - 새 사용자 생성
                logging.debug(
                    f"사용자 '{smb_username}'가 존재하지 않습니다. NFS UID/GID로 사용자를 생성합니다.")
                try:
                    if existing_group:
                        # 이미 해당 GID를 가진 그룹이 있으면 그룹 생성 건너뜀
                        logging.debug(
                            f"GID {self.nfs_gid}를 가진 그룹 '{existing_group}'이(가) 이미 존재합니다. 이 그룹을 사용합니다.")
                    else:
                        # 그룹이 존재하지 않으면 새로 생성
                        group_result = subprocess.run(['groupadd', '-r', '-g', str(
                            self.nfs_gid), smb_username], capture_output=True, text=True, check=False)
                        if group_result.returncode != 0:
                            logging.warning(
                                f"그룹 생성 실패: {group_result.stderr.strip()}")
                        else:
                            existing_group = smb_username
                            logging.debug(
                                f"그룹 '{smb_username}' 생성 완료 (GID={self.nfs_gid})")

                    # 사용자 생성 (지정된 UID와 찾은 또는 생성된 그룹 사용)
                    group_to_use = existing_group if existing_group else str(
                        self.nfs_gid)
                    user_result = subprocess.run(['useradd', '-r', '-u', str(
                        self.nfs_uid), '-g', group_to_use, smb_username], capture_output=True, text=True, check=False)
                    logging.debug(
                        f"사용자 생성 결과: {user_result.returncode}, 오류: {user_result.stderr.strip() if user_result.stderr else '없음'}")

                    # SMB 패스워드 설정
                    if smb_password:
                        proc = subprocess.Popen(
                            ['passwd', smb_username], stdin=subprocess.PIPE)
                        proc.communicate(
                            input=f"{smb_password}\n{smb_password}\n".encode())
                        logging.debug(f"사용자 '{smb_username}' 패스워드 설정 완료")

                    logging.debug(
                        f"사용자 '{smb_username}' 생성 완료 (UID={self.nfs_uid}, GID={self.nfs_gid})")
                except Exception as e:
                    logging.error(f"사용자 생성 중 오류 발생: {e}")
            elif not uid_gid_match:
                # 2. 사용자는 있지만 UID/GID가 일치하지 않음 - Samba 서비스 중지 후 UID/GID 수정
                logging.debug(
                    f"사용자 '{smb_username}'의 UID/GID가 NFS와 일치하지 않아 수정합니다.")

                # Samba 서비스 중지
                try:
                    logging.debug("Samba 서비스 중지...")
                    self.stop_samba_service()

                    # 사용자 UID 수정
                    usermod_result = subprocess.run(
                        ['usermod', '-u', str(self.nfs_uid), smb_username], capture_output=True, text=True, check=False)
                    logging.debug(
                        f"사용자 UID 수정 결과: {usermod_result.returncode}, 오류: {usermod_result.stderr.strip() if usermod_result.stderr else '없음'}")

                    # 사용자 GID 수정 (기존 그룹 사용)
                    group_to_use = existing_group if existing_group else str(
                        self.nfs_gid)
                    groupmod_result = subprocess.run(
                        ['usermod', '-g', group_to_use, smb_username], capture_output=True, text=True, check=False)
                    logging.debug(
                        f"사용자 GID 수정 결과: {groupmod_result.returncode}, 오류: {groupmod_result.stderr.strip() if groupmod_result.stderr else '없음'}")

                    # 필요한 파일의 소유권 변경
                    group_name = existing_group if existing_group else smb_username
                    subprocess.run(
                        ['chown', '-R', f"{smb_username}:{group_name}", self.links_dir], check=False)
                    logging.debug(
                        f"사용자 '{smb_username}'의 UID/GID 수정 완료 (UID={self.nfs_uid}, GID={self.nfs_gid})")
                except Exception as e:
                    logging.error(f"사용자 UID/GID 수정 중 오류 발생: {e}")
            else:
                # 3. 사용자가 있고 UID/GID도 일치함 - 작업 없음
                logging.debug(f"사용자 '{smb_username}'의 UID/GID가 이미 NFS와 일치합니다.")

            # 서비스 시작 (요청된 경우)
            if start_service:
                try:
                    logging.debug("Samba 서비스 시작...")
                    self.start_samba_service()
                    logging.debug("Samba 서비스 시작 완료")
                except Exception as e:
                    logging.error(f"Samba 서비스 시작 중 오류 발생: {e}")

        except Exception as e:
            logging.error(f"SMB 사용자의 UID/GID 설정 실패: {e}")
            raise

    def set_links_directory_permissions(self, links_dir: str) -> None:
        """공유용 링크 디렉토리 권한 설정"""
        try:
            if not os.path.exists(links_dir):
                os.makedirs(links_dir, mode=0o755)
                logging.debug(f"공유용 링크 디렉토리 생성됨: {links_dir}")
            else:
                # 이미 존재하는 경우 권한 설정
                os.chmod(links_dir, 0o755)

            try:
                # NFS GID가 이미 존재하는 그룹에 할당되어 있는지 확인
                existing_group = None
                try:
                    group_info = subprocess.run(['getent', 'group', str(
                        self.nfs_gid)], capture_output=True, text=True)
                    if group_info.returncode == 0:  # 해당 GID를 가진 그룹이 존재
                        existing_group = group_info.stdout.strip().split(':')[
                            0]
                except Exception as e:
                    logging.error(f"그룹 확인 중 오류: {e}")

                # 적절한 그룹 이름 결정
                group_name = existing_group if existing_group else self.config.SMB_USERNAME

                # 디렉토리 소유권 설정
                subprocess.run(
                    ['chown', f"{self.config.SMB_USERNAME}:{group_name}", links_dir], check=True)
                logging.debug("공유용 링크 디렉토리 권한 설정 완료")
            except subprocess.CalledProcessError as e:
                logging.error(f"디렉토리 소유권 변경 실패: {e}")
        except Exception as e:
            logging.error(f"공유용 링크 디렉토리 생성/설정 실패: {e}")
            raise

    def cleanup(self) -> None:
        """SMB 관련 리소스 정리"""
        try:
            # Samba 서비스 중지
            self.stop_samba_service(timeout=30)
            logging.debug("SMB 서비스 중지 완료")
        except Exception as e:
            logging.error(f"SMB 서비스 정리 중 오류 발생: {e}")

    def remove_symlink(self, subfolder: str) -> bool:
        """
        특정 폴더의 심볼릭 링크 제거

        Args:
            subfolder: 제거할 심볼릭 링크 서브폴더 경로

        Returns:
            bool: 제거 성공 여부
        """
        try:
            link_path = os.path.join(
                self.links_dir, subfolder.replace(os.sep, '_'))
            if os.path.exists(link_path):
                os.remove(link_path)
                logging.debug(f"심볼릭 링크 제거됨: {link_path}")
            return True
        except Exception as e:
            logging.error(f"심볼릭 링크 제거 실패 ({subfolder}): {e}")
            return False

    def create_symlink(self, subfolder: str) -> bool:
        """
        특정 폴더의 심볼릭 링크 생성

        Args:
            subfolder: 생성할 심볼릭 링크 서브폴더 경로
            source_base_path: 원본 경로의 기본 경로

        Returns:
            bool: 생성 성공 여부
        """
        try:
            source_path = os.path.join(self.config.MOUNT_PATH, subfolder)
            link_path = os.path.join(
                self.links_dir, subfolder.replace(os.sep, '_'))

            # 이미 존재하는 링크 제거
            if os.path.exists(link_path):
                os.remove(link_path)

            # 심볼릭 링크 생성
            os.symlink(source_path, link_path)

            logging.debug(f"심볼릭 링크 생성됨: {link_path} -> {source_path}")
            return True
        except Exception as e:
            logging.error(f"심볼릭 링크 생성 실패 ({subfolder}): {e}")
            return False

    def cleanup_all_symlinks(self) -> None:
        """
        links_dir 디렉토리에 있는 모든 심볼릭 링크를 제거합니다.
        """
        try:
            if os.path.exists(self.links_dir):
                for filename in os.listdir(self.links_dir):
                    file_path = os.path.join(self.links_dir, filename)
                    if os.path.islink(file_path):
                        try:
                            os.remove(file_path)
                            logging.debug(f"심볼릭 링크 제거됨: {file_path}")
                        except Exception as e:
                            logging.error(f"심볼릭 링크 제거 실패 ({file_path}): {e}")
                logging.debug(f"모든 심볼릭 링크 제거 완료: {self.links_dir}")
        except Exception as e:
            logging.error(f"심볼릭 링크 정리 중 오류 발생: {e}")
