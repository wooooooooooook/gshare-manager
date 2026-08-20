import logging
import os
import subprocess
from typing import Optional, Tuple

DEFAULT_NFS_OPTIONS = "nolock,vers=3,hard,timeo=600,retrans=5,actimeo=30"
DEFAULT_NFS_TIMEOUT = 30
FALLBACK_NFS_OPTIONS = [
    "vers=3,nolock",
    "vers=3"
]


def build_nfs_mount_command(
    nfs_path: str,
    target_path: str,
    options: str = DEFAULT_NFS_OPTIONS
) -> list:
    """NFS 마운트 명령어 리스트 생성"""
    return ['mount', '-t', 'nfs', '-o', options, nfs_path, target_path]


def is_nfs_mount_present(mount_path: str, nfs_path: Optional[str] = None) -> bool:
    """
    /proc/mounts 기준으로 mount_path에 nfs/nfs4 마운트 여부를 확인한다.
    nfs_path가 주어지면 해당 NFS export와 일치하는지도 검증한다.
    """
    try:
        target_mount = os.path.realpath(mount_path)
        target_nfs = nfs_path.rstrip('/') if nfs_path else None

        with open('/proc/mounts', 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue

                source = parts[0].replace('\\040', ' ')
                target = parts[1].replace('\\040', ' ')
                fs_type = parts[2]

                if fs_type not in ('nfs', 'nfs4'):
                    continue
                if os.path.realpath(target) != target_mount:
                    continue

                # NAS 주소는 IP/호스트명으로 다르게 설정될 수 있어 export 경로 기준으로도 허용한다.
                if target_nfs:
                    mounted_source = source.rstrip('/')
                    target_source = target_nfs.rstrip('/')
                    if mounted_source != target_source:
                        mounted_export = mounted_source.split(':', 1)[-1]
                        target_export = target_source.split(':', 1)[-1]
                        if mounted_export != target_export:
                            continue

                return True
    except Exception as e:
        logging.debug(f"/proc/mounts 기반 NFS 마운트 확인 실패 ({mount_path}): {e}")

    return False


def find_nfs_mount_point(nfs_path: str) -> Optional[str]:
    """
    /proc/mounts에서 nfs_path (또는 동일 export)가 마운트된 마운트 지점 경로를 탐색한다.
    마운트 지점이 존재하면 해당 경로(str)를 반환하고, 없으면 None을 반환한다.
    """
    if not nfs_path:
        return None

    try:
        target_nfs = nfs_path.rstrip('/')
        target_export = target_nfs.split(':', 1)[-1] if ':' in target_nfs else target_nfs

        with open('/proc/mounts', 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue

                source = parts[0].replace('\\040', ' ')
                target = parts[1].replace('\\040', ' ')
                fs_type = parts[2]

                if fs_type not in ('nfs', 'nfs4'):
                    continue

                mounted_source = source.rstrip('/')
                mounted_export = mounted_source.split(':', 1)[-1] if ':' in mounted_source else mounted_source

                if mounted_source == target_nfs or mounted_export == target_export:
                    return target
    except Exception as e:
        logging.debug(f"/proc/mounts 탐색 중 오류: {e}")

    return None


def mount_nfs(
    nfs_path: str,
    target_path: str,
    options: str = DEFAULT_NFS_OPTIONS,
    timeout: int = DEFAULT_NFS_TIMEOUT
) -> Tuple[bool, str]:
    """
    NFS 마운트를 실행하고 성공 여부와 오류 메시지/출력을 반환한다.
    기본 옵션 시도 실패 시 NFSv3 호환 대체 옵션들을 순차적으로 시도한다.
    Returns:
        (success: bool, message: str)
    """
    if not nfs_path or not target_path:
        return False, "NFS 경로 또는 타겟 마운트 경로가 지정되지 않았습니다."

    if not os.path.exists(target_path):
        try:
            os.makedirs(target_path, exist_ok=True)
        except Exception as e:
            return False, f"마운트 타겟 디렉토리 생성 실패: {e}"

    # 시도할 옵션 목록 구성 (전달받은 options를 1순위로, 실패 시 FALLBACK 옵션 시도)
    options_to_try = [options]
    if options == DEFAULT_NFS_OPTIONS:
        for fb_opt in FALLBACK_NFS_OPTIONS:
            if fb_opt not in options_to_try:
                options_to_try.append(fb_opt)

    last_error_msg = ""
    for opt in options_to_try:
        cmd = build_nfs_mount_command(nfs_path, target_path, opt)
        logging.debug(f"NFS 마운트 실행 명령어 (옵션: {opt}): {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                logging.info(f"NFS 마운트 성공: {nfs_path} -> {target_path} (사용된 옵션: {opt})")
                return True, result.stdout.strip()
            else:
                stderr_msg = result.stderr.strip() if result.stderr else result.stdout.strip()
                last_error_msg = stderr_msg if stderr_msg else f"mount command failed with return code {result.returncode}"
                logging.warning(f"NFS 마운트 시도 실패 (옵션: {opt}): {last_error_msg}")
        except subprocess.TimeoutExpired:
            last_error_msg = f"NFS 마운트 시도 시간 초과 ({timeout}초)"
            logging.warning(f"NFS 마운트 시도 실패 (옵션: {opt}): {last_error_msg}")
        except Exception as e:
            last_error_msg = f"NFS 마운트 실행 중 예외 발생: {e}"
            logging.warning(f"NFS 마운트 시도 실패 (옵션: {opt}): {last_error_msg}")

    logging.error(f"NFS 마운트 최종 실패 (경로: {nfs_path}): {last_error_msg}")
    return False, last_error_msg


def unmount_nfs(
    target_path: str,
    lazy: bool = True,
    force: bool = False,
    timeout: int = 15
) -> Tuple[bool, str]:
    """
    NFS 마운트 해제를 실행한다.
    Returns:
        (success: bool, message: str)
    """
    if not target_path:
        return False, "타겟 경로가 지정되지 않았습니다."

    cmd = ['umount']
    if force:
        cmd.append('-f')
    if lazy:
        cmd.append('-l')
    cmd.append(target_path)

    logging.debug(f"NFS 언마운트 실행 명령어: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            logging.info(f"NFS 마운트 해제 성공: {target_path}")
            return True, result.stdout.strip()
        else:
            stderr_msg = result.stderr.strip() if result.stderr else result.stdout.strip()
            error_msg = stderr_msg if stderr_msg else f"umount failed with return code {result.returncode}"
            logging.warning(f"NFS 마운트 해제 실패 ({target_path}): {error_msg}")
            return False, error_msg
    except subprocess.TimeoutExpired:
        error_msg = f"NFS 마운트 해제 시간 초과 ({timeout}초)"
        logging.warning(f"NFS 마운트 해제 실패 ({target_path}): {error_msg}")
        return False, error_msg
    except Exception as e:
        error_msg = f"NFS 마운트 해제 중 예외 발생: {e}"
        logging.warning(f"NFS 마운트 해제 실패 ({target_path}): {error_msg}")
        return False, error_msg
