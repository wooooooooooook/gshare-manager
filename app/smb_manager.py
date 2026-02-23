import logging
import os
import subprocess
import time
from config import GshareConfig  # type: ignore
from typing import Optional, Tuple


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
        self.user_checked = False # 사용자 검증 완료 여부 
        self._active_links = set()  # Active link cache

        # 초기화 작업
        self._init_smb_config()
        # 초기 상태 설정 (파일에서 확인)
        self._is_smb_active = self._check_smb_status_from_file()
        self._set_smb_user_ownership()

        # 공유용 링크 디렉토리 생성 및 권한 설정
        self._set_links_directory_permissions(self.links_dir)

        # 시작 시 기존 심볼릭 링크 모두 제거
        self.cleanup_all_symlinks()

    def is_link_active(self, subfolder: str) -> bool:
        """
        Check if a link is active using memory cache to minimize syscalls.

        Args:
            subfolder: Original subfolder path (e.g., 'Movies/Action')
        """
        link_name = subfolder.replace(os.sep, '_')
        return link_name in self._active_links

    def _init_smb_config(self) -> None:
        """기본 SMB 설정 초기화"""
        try:
            # 기본 설정 생성
            base_config = f"""[global]
   workgroup = WORKGROUP
   server string = Samba Server
   server role = standalone server
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no
   smb ports = {self.config.SMB_PORT}
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
   # 디버깅 설정
   log level = 3
"""
            # 기본 설정 저장
            with open('/etc/samba/smb.conf', 'w') as f:
                f.write(base_config)

            # 기존 Samba 사용자 존재 여부 확인
            user_in_samba = subprocess.run(['pdbedit', '-L', self.config.SMB_USERNAME], 
                                       capture_output=True).returncode == 0
            
            # 시스템에 사용자 존재 여부 확인
            user_exists = self._get_user_info(self.config.SMB_USERNAME) is not None
            
            # 사용자 관리 단계별 처리
            if user_in_samba:
                # 1. Samba 사용자가 이미 존재하면 삭제 후 다시 생성
                logging.debug(f"기존 Samba 사용자 {self.config.SMB_USERNAME} 삭제 시도")
                subprocess.run(['smbpasswd', '-x', self.config.SMB_USERNAME], 
                            capture_output=True, check=False)
            
            if user_exists:
                # 2. 시스템 사용자가 존재하면 패스워드 설정
                passwd_cmd = f"{self.config.SMB_PASSWORD}\n{self.config.SMB_PASSWORD}\n".encode()
                result = subprocess.run(['smbpasswd', '-a', '-s', self.config.SMB_USERNAME],
                                     input=passwd_cmd, capture_output=True)
                
                if result.returncode == 0:
                    logging.debug(f"Samba 사용자 {self.config.SMB_USERNAME} 비밀번호 설정 성공")
                else:
                    logging.error(f"Samba 사용자 비밀번호 설정 실패: {result.stderr.decode()}")
                
                # 사용자 활성화
                subprocess.run(['smbpasswd', '-e', self.config.SMB_USERNAME], check=False)
                logging.debug(f"Samba 사용자 {self.config.SMB_USERNAME} 활성화")
            else:
                logging.debug(f"시스템에 {self.config.SMB_USERNAME} 사용자가 존재하지 않습니다.")
                # 사용자 생성은 _set_smb_user_ownership 메서드에서 수행됨
            
            logging.debug("SMB 기본 설정 초기화 완료")
        except Exception as e:
            logging.error(f"SMB 기본 설정 초기화 실패: {e}")
            raise

    def _check_samba_process_status(self) -> bool:
        """smbd 프로세스가 실행 중인지 확인"""
        try:
            result = subprocess.run(
                ['pgrep', '-x', 'smbd'],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode == 0
        except Exception as e:
            logging.error(f"Samba 프로세스 상태 확인 중 오류: {e}")
            return False

    def check_smb_status(self) -> bool:
        """SMB 공유 설정 및 smbd 프로세스 실행 여부를 함께 확인"""
        config_active = self._check_smb_status_from_file()
        process_active = self._check_samba_process_status()
        self._is_smb_active = config_active and process_active
        return self._is_smb_active

    def _check_smb_status_from_file(self) -> bool:
        """SMB 설정 파일에서 실제 공유 설정 여부 확인"""
        try:
            if not os.path.exists('/etc/samba/smb.conf'):
                return False
            with open('/etc/samba/smb.conf', 'r') as f:
                content = f.read()
                share_name = self.config.SMB_SHARE_NAME
                return f"[{share_name}]" in content
        except Exception as e:
            logging.error(f"SMB 설정 파일 확인 중 오류: {e}")
            return False

    def _start_samba_service(self) -> None:
        """Samba 서비스 시작"""
        try:
            # 먼저 잔여 프로세스 확인 및 종료
            self._stop_samba_service()
            
            # Docker 환경에서는 직접 데몬 실행
            subprocess.run(['smbd', '--daemon'], check=False)
            subprocess.run(['nmbd', '--daemon'], check=False)
            
            # 서비스 시작 확인
            time.sleep(1)
            if not self._check_samba_process_status():
                logging.warning("Samba 서비스 시작 후에도 실행되지 않는 것으로 확인됩니다.")
                
        except Exception as e:
            logging.error(f"Samba 서비스 시작 실패: {e}")
            raise

    def _stop_samba_service(self, timeout=None) -> None:
        """Samba 서비스 중지"""
        try:
            # 프로세스 종료
            subprocess.run(['pkill', 'smbd'], check=False)
            subprocess.run(['pkill', 'nmbd'], check=False)
        except Exception as e:
            logging.error(f"Samba 서비스 중지 실패: {e}")

    def _restart_samba_service(self) -> None:
        """Samba 서비스 재시작"""
        try:
            self._stop_samba_service()
            time.sleep(1)  # 잠시 대기
            self._start_samba_service()
        except Exception as e:
            logging.error(f"Samba 서비스 재시작 실패: {e}")
            raise

    def _update_smb_config(self) -> None:
        """SMB 설정 파일 업데이트"""
        try:
            # 사용자 체크가 필요한 경우에만 SMB 사용자 UID/GID 설정을 업데이트
            if not self.user_checked:
                self._set_smb_user_ownership()
            
            share_name = self.config.SMB_SHARE_NAME

            # 설정 파일 읽기
            with open('/etc/samba/smb.conf', 'r') as f:
                lines = f.readlines()

            # 빈 줄 제거
            lines = [line for line in lines if line.strip()]
            
            # global 섹션만 유지하고 다른 섹션은 제거
            global_section_lines = []
            for line in lines:
                if line.strip().startswith('[') and line.strip() != '[global]':
                    break
                global_section_lines.append(line)

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
   veto files = /@*
   hide dot files = yes
   delete veto files = no
"""
            # global 섹션 + 공유 설정
            final_lines = global_section_lines + [share_config]

            # 설정 파일 저장
            with open('/etc/samba/smb.conf', 'w') as f:
                f.writelines([line for line in final_lines if line.strip()])

            self._is_smb_active = True
            logging.debug(
                f"SMB 설정 파일이 업데이트 되었습니다: {share_name}")
        except Exception as e:
            logging.error(f"SMB 설정 파일 업데이트 실패: {e}")
            raise

    def activate_smb_share(self) -> bool:
        """SMB 공유 활성화 - SMB 설정 파일 업데이트 및 서비스 재시작"""
        try:
            # 이미 SMB 서비스가 실행 중인지 확인
            is_running = self.check_smb_status()

            # SMB 설정 파일 업데이트
            self._update_smb_config()
            
            # SMB 사용자 및 비밀번호 설정 재확인 (첫 실행 시에만)
            if not self.user_checked:
                logging.debug("SMB 공유 활성화 시 사용자 설정 재확인")
                self._set_smb_user_ownership()
                self.user_checked = True
            
            # 심볼릭 링크 소유권 수정
            self._fix_symlinks_ownership()

            # 서비스 재시작/시작
            if is_running:
                logging.debug("SMB 서비스가 이미 실행 중입니다. 서비스를 재시작합니다.")
                self._restart_samba_service()
            else:
                self._start_samba_service()

            # 서비스 상태 확인
            if self.check_smb_status():
                logging.info("SMB 공유가 활성화되었습니다.")
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
                f.writelines([line for line in new_lines if line.strip()])

            self._is_smb_active = False
            # Samba 서비스 중지
            self._stop_samba_service()
            
            # 심볼릭 링크 제거
            self.cleanup_all_symlinks()

            logging.info("SMB 공유 비활성화 성공")
            return True
        except Exception as e:
            logging.error(f"SMB 공유 비활성화 실패: {e}")
            return False


    def _get_user_info(self, username: str) -> Optional[Tuple[int, int]]:
        """
        사용자의 UID와 GID를 반환합니다.

        Args:
            username: 사용자 이름

        Returns:
            Optional[Tuple[int, int]]: (UID, GID) 튜플 (존재하지 않거나 오류 시 None)
        """
        try:
            user_info = subprocess.run(
                ['id', username], capture_output=True, text=True)
            if user_info.returncode == 0:
                info_parts = user_info.stdout.strip().split()
                current_uid = int(
                    info_parts[0].split('=')[1].split('(')[0])
                current_gid = int(
                    info_parts[1].split('=')[1].split('(')[0])
                return current_uid, current_gid
        except Exception as e:
            logging.error(f"사용자 확인 중 오류 ({username}): {e}")
        return None

    def _get_group_name(self, gid: int) -> Optional[str]:
        """
        GID에 해당하는 그룹 이름을 반환합니다.

        Args:
            gid: 그룹 ID

        Returns:
            Optional[str]: 그룹 이름 (존재하지 않거나 오류 시 None)
        """
        try:
            group_info = subprocess.run(
                ['getent', 'group', str(gid)], capture_output=True, text=True)
            if group_info.returncode == 0:  # 해당 GID를 가진 그룹이 존재
                return group_info.stdout.strip().split(':')[0]
        except Exception as e:
            logging.error(f"그룹 확인 중 오류 ({gid}): {e}")
        return None
    def _set_smb_user_ownership(self) -> None:
        """SMB 사용자의 UID/GID를 NFS 마운트 경로와 동일하게 설정"""
        try:
            # 이미 확인된 경우 건너뜀
            if self.user_checked:
                logging.debug("SMB 사용자 이미 확인됨. 사용자 설정 건너뜀")
                return

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
                user_info_tuple = self._get_user_info(smb_username)
                if user_info_tuple:  # 사용자 존재
                    user_exists = True
                    current_uid, current_gid = user_info_tuple
                    uid_gid_match = (
                        current_uid == self.nfs_uid and current_gid == self.nfs_gid)
                    logging.debug(
                        f"사용자 '{smb_username}' 존재: UID={current_uid}, GID={current_gid}")
                    logging.debug(
                        f"NFS UID/GID와 일치: {uid_gid_match} (NFS: UID={self.nfs_uid}, GID={self.nfs_gid})")
            except Exception as e:
                logging.error(f"사용자 확인 중 오류: {e}")

            # NFS GID가 이미 존재하는 그룹에 할당되어 있는지 확인
            existing_group = self._get_group_name(self.nfs_gid)
            if existing_group:
                logging.debug(f"GID {self.nfs_gid}를 가진 기존 그룹 발견: {existing_group}")

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
                        group_result = subprocess.run(['groupadd', '-r', '-o', '-g', str(
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
                    user_cmd = ['useradd', '-r', '-o', '-u', str(self.nfs_uid), '-g', group_to_use, smb_username]
                    user_result = subprocess.run(user_cmd, capture_output=True, text=True, check=False)
                    if user_result.returncode != 0:
                        logging.error(f"사용자 생성 실패: {user_result.stderr.strip()}")
                    else:
                        logging.debug(f"사용자 생성 성공: {smb_username} (UID={self.nfs_uid}, GID={self.nfs_gid})")

                    # SMB 패스워드 설정
                    if smb_password:
                        # 시스템 사용자 패스워드 설정
                        proc = subprocess.Popen(
                            ['passwd', smb_username], stdin=subprocess.PIPE)
                        proc.communicate(
                            input=f"{smb_password}\n{smb_password}\n".encode())
                        
                        # Samba 사용자 패스워드 설정 (추가)
                        passwd_cmd = f"{smb_password}\n{smb_password}\n".encode()
                        smb_result = subprocess.run(['smbpasswd', '-a', '-s', smb_username],
                                                 input=passwd_cmd, capture_output=True)
                        if smb_result.returncode == 0:
                            subprocess.run(['smbpasswd', '-e', smb_username], check=False)
                            logging.debug(f"Samba 사용자 '{smb_username}' 패스워드 설정 및 활성화 완료")
                        else:
                            logging.error(f"Samba 사용자 패스워드 설정 실패: {smb_result.stderr.decode()}")

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
                    self._stop_samba_service()

                    # 사용자 UID 수정
                    usermod_result = subprocess.run(
                        ['usermod', '-o', '-u', str(self.nfs_uid), smb_username], capture_output=True, text=True, check=False)
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
                    
                    # SMB 패스워드 다시 설정 (추가)
                    if smb_password:
                        passwd_cmd = f"{smb_password}\n{smb_password}\n".encode()
                        # 기존 사용자 삭제 후 다시 추가
                        subprocess.run(['smbpasswd', '-x', smb_username], capture_output=True, check=False)
                        smb_result = subprocess.run(['smbpasswd', '-a', '-s', smb_username],
                                                 input=passwd_cmd, capture_output=True)
                        if smb_result.returncode == 0:
                            subprocess.run(['smbpasswd', '-e', smb_username], check=False)
                            logging.debug(f"Samba 사용자 '{smb_username}' 패스워드 재설정 및 활성화 완료")
                        else:
                            logging.error(f"Samba 사용자 패스워드 재설정 실패: {smb_result.stderr.decode()}")
                    
                except Exception as e:
                    logging.error(f"사용자 UID/GID 수정 중 오류 발생: {e}")
            else:
                # 3. 사용자가 있고 UID/GID도 일치함 - Samba 비밀번호 확인 및 재설정
                logging.debug(f"사용자 '{smb_username}'의 UID/GID가 이미 NFS와 일치합니다.")
                
                # Samba 사용자 비밀번호 재설정 추가 (중요!)
                if smb_password:
                    try:
                        # 기존 Samba 사용자 존재 여부 확인
                        user_in_samba = subprocess.run(['pdbedit', '-L', smb_username], 
                                                     capture_output=True).returncode == 0
                        
                        if user_in_samba:
                            # Samba 사용자가 이미 존재하면 삭제 후 다시 생성
                            subprocess.run(['smbpasswd', '-x', smb_username], capture_output=True, check=False)
                        
                        # Samba 사용자 추가 및 비밀번호 설정
                        passwd_cmd = f"{smb_password}\n{smb_password}\n".encode()
                        smb_result = subprocess.run(['smbpasswd', '-a', '-s', smb_username],
                                                 input=passwd_cmd, capture_output=True)
                        
                        if smb_result.returncode == 0:
                            subprocess.run(['smbpasswd', '-e', smb_username], check=False)
                            logging.debug(f"Samba 사용자 '{smb_username}' 패스워드 재설정 및 활성화 완료")
                        else:
                            logging.error(f"Samba 사용자 패스워드 재설정 실패: {smb_result.stderr.decode()}")
                    except Exception as e:
                        logging.error(f"Samba 사용자 비밀번호 설정 중 오류 발생: {e}")

            # 작업 완료 후 사용자 체크 플래그 설정
            self.user_checked = True

        except Exception as e:
            logging.error(f"SMB 사용자의 UID/GID 설정 실패: {e}")
            raise

    def _set_links_directory_permissions(self, links_dir: str) -> None:
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
                existing_group = self._get_group_name(self.nfs_gid)

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

    def remove_symlink(self, subfolder: str) -> bool:
        """
        특정 폴더의 심볼릭 링크 제거

        Args:
            subfolder: 제거할 심볼릭 링크 서브폴더 경로

        Returns:
            bool: 제거 성공 여부
        """
        try:
            link_name = subfolder.replace(os.sep, '_')
            link_path = os.path.join(self.links_dir, link_name)
            if os.path.exists(link_path):
                os.remove(link_path)
                logging.info(f"심볼릭 링크 제거됨: {link_path}")

            self._active_links.discard(link_name)

            # 성능 최적화: 매 삭제마다 links_dir 전체 스캔(os.listdir + os.path.islink)을
            # 피하고 메모리 캐시(self._active_links)로 남은 링크 여부를 우선 판단합니다.
            # 측정(로컬 micro-benchmark 기준): 10,000개 링크 환경에서
            # 디스크 스캔 O(n) 대비 set 길이 확인 O(1)로 체크 시간이 유의미하게 감소합니다.
            remaining_symlinks = len(self._active_links) > 0

            # 캐시가 비어있더라도 외부 프로세스가 링크를 생성했을 가능성은 남아있으므로
            # 비활성화 직전 1회만 디스크를 재확인해 정확성을 유지합니다.
            if not remaining_symlinks and os.path.exists(self.links_dir):
                remaining_symlinks = any(
                    os.path.islink(os.path.join(self.links_dir, filename))
                    for filename in os.listdir(self.links_dir)
                )
            
            if not remaining_symlinks:
                # 모니터링 루프 블로킹을 피하기 위해 지연 대기 없이 즉시 비활성화
                logging.info("남은 심볼릭 링크가 없어 SMB 공유를 비활성화합니다.")
                self.deactivate_smb_share()

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
            
            # SMB 사용자의 소유권으로 변경
            try:
                # NFS GID가 이미 존재하는 그룹에 할당되어 있는지 확인
                existing_group = self._get_group_name(self.nfs_gid)

                # 적절한 그룹 이름 결정
                group_name = existing_group if existing_group else self.config.SMB_USERNAME
                
                # 심볼릭 링크 소유권 변경
                subprocess.run(
                    ['chown', '-h', f"{self.config.SMB_USERNAME}:{group_name}", link_path], check=False)
                logging.debug(f"심볼릭 링크 소유권 변경됨: {link_path}")
            except Exception as e:
                logging.warning(f"심볼릭 링크 소유권 변경 실패 ({link_path}): {e}")

            self._active_links.add(subfolder.replace(os.sep, "_"))
            logging.info(f"심볼릭 링크 생성됨: {link_path} -> {source_path}")
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

            self._active_links.clear()
            logging.info(f"모든 심볼릭 링크 제거 완료: {self.links_dir}")

        except Exception as e:
            logging.error(f"심볼릭 링크 정리 중 오류 발생: {e}")
            
    def _fix_symlinks_ownership(self) -> None:
        """
        기존 심볼릭 링크의 소유권을 SMB 사용자로 변경합니다.
        """
        try:
            if os.path.exists(self.links_dir):
                # NFS GID가 이미 존재하는 그룹에 할당되어 있는지 확인
                existing_group = self._get_group_name(self.nfs_gid)

                # 적절한 그룹 이름 결정
                group_name = existing_group if existing_group else self.config.SMB_USERNAME
                
                # 모든 심볼릭 링크 소유권 변경
                for filename in os.listdir(self.links_dir):
                    file_path = os.path.join(self.links_dir, filename)
                    if os.path.islink(file_path):
                        try:
                            subprocess.run(
                                ['chown', '-h', f"{self.config.SMB_USERNAME}:{group_name}", file_path], check=False)
                            logging.debug(f"심볼릭 링크 소유권 변경됨: {file_path}")
                        except Exception as e:
                            logging.warning(f"심볼릭 링크 소유권 변경 실패 ({file_path}): {e}")
                            
                logging.debug(f"모든 심볼릭 링크 소유권 변경 완료: {self.links_dir}")
        except Exception as e:
            logging.error(f"심볼릭 링크 소유권 변경 중 오류 발생: {e}")
