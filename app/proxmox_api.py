import logging
import time
import requests  # type: ignore
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional
from config import GshareConfig  # type: ignore


class ProxmoxAPI:
    def __init__(self, config: GshareConfig):
        self.config = config
        self.session = requests.Session()
        self.session.verify = False

        # 재시도 로직 설정 (총 3회, 1초 대기)
        try:
            # urllib3 >= 1.26.0
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[500, 502, 503, 504],
                allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
            )
        except TypeError:
            # urllib3 < 1.26.0
            logging.warning("Detected older urllib3 version, falling back to method_whitelist")
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[500, 502, 503, 504],
                method_whitelist=["HEAD", "GET", "OPTIONS", "POST"]
            )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._set_token_auth()

        # Cache for VM status
        self._cached_status = None
        self._last_status_check = 0

    def _set_token_auth(self) -> None:
        # API 토큰을 사용하여 인증 헤더 설정
        self.session.headers.update({
            "Authorization": f"PVEAPIToken={self.config.TOKEN_ID}={self.config.SECRET}"
        })
        logging.debug("Proxmox API 토큰 인증 설정 완료")

    def _get_vm_status_data(self) -> dict:
        now = time.time()
        # 2초 캐시 (모니터링 루프 내 중복 호출 방지)
        if self._cached_status and (now - self._last_status_check < 2.0):
            return self._cached_status

        response = self.session.get(
            f"{self.config.PROXMOX_HOST}/nodes/{self.config.NODE_NAME}/qemu/{self.config.VM_ID}/status/current",
            timeout=(self.config.PROXMOX_TIMEOUT, 10)
        )
        response.raise_for_status()
        data = response.json()["data"]

        self._cached_status = data
        self._last_status_check = now
        return data

    def is_vm_running(self) -> bool:
        try:
            data = self._get_vm_status_data()
            result = data["status"]
            logging.debug(f"VM 상태 확인 응답: {result}")
            return result == "running"
        except Exception as e:
            logging.error(f"VM 상태 확인 실패: {e}")
            return False

    def get_vm_uptime(self) -> Optional[float]:
        try:
            data = self._get_vm_status_data()
            result = data["uptime"]
            logging.debug(f"VM 부팅 시간 확인 응답: {result}")
            return result
        except Exception as e:
            logging.error(f"VM 부팅 시간 확인 실패: {e}")
            return None

    def get_cpu_usage(self) -> Optional[float]:
        try:
            data = self._get_vm_status_data()
            result = data["cpu"] * 100
            logging.debug(f"CPU 사용량 확인 응답: {result}")
            return result
        except Exception as e:
            logging.error(f"CPU 사용량 확인 실패: {e}")
            return None

    def start_vm(self) -> bool:
        try:
            response = self.session.post(
                f"{self.config.PROXMOX_HOST}/nodes/{self.config.NODE_NAME}/qemu/{self.config.VM_ID}/status/start",
                timeout=(self.config.PROXMOX_TIMEOUT, 10)
            )
            response.raise_for_status()
            logging.debug(f"VM 시작 응답 받음")

            return True
        except Exception as e:
            return False
