import logging
import threading
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
        # urllib3 >= 1.26.0 required
        # POST는 재시도 대상에서 제외한다. start/stop 명령이 500/502/503/504 응답 후
        # 자동 재시도되면 Proxmox에서 VM 시작/종료 명령이 중복 전송될 수 있다.
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._set_token_auth()

        # Cache for VM status
        self._cached_status = None
        self._last_status_check = 0

        # VM 시작 중복 요청 방지
        # start_vm()은 락으로 직렬화하고, 마지막 start 전송 후 쿨다운 동안 들어오는
        # 요청은 실제 상태를 다시 확인한 뒤 중복 전송을 생략한다.
        self._vm_action_lock = threading.Lock()
        self._last_start_attempt = 0.0
        self._start_cooldown = 15.0

    def _set_token_auth(self) -> None:
        # API 토큰을 사용하여 인증 헤더 설정
        self.session.headers.update({
            "Authorization": f"PVEAPIToken={self.config.TOKEN_ID}={self.config.SECRET}"
        })
        logging.debug("Proxmox API 토큰 인증 설정 완료")

    def _request(self, method: str, endpoint: str) -> requests.Response:
        url = f"{self.config.PROXMOX_HOST}/nodes/{self.config.NODE_NAME}/qemu/{self.config.VM_ID}/{endpoint}"
        response = self.session.request(
            method=method,
            url=url,
            timeout=(self.config.PROXMOX_TIMEOUT, 10)
        )
        response.raise_for_status()
        return response

    def _invalidate_status_cache(self) -> None:
        """VM 상태 캐시를 무효화한다. VM 시작/종료 직후 호출해 오래된 상태를 재사용하지 않게 한다."""
        self._cached_status = None
        self._last_status_check = 0.0

    def _get_vm_status_data(self, force_refresh: bool = False) -> dict:
        now = time.time()
        # 2초 캐시 (모니터링 루프 내 중복 호출 방지)
        if not force_refresh and self._cached_status and (now - self._last_status_check < 2.0):
            return self._cached_status

        response = self._request("GET", "status/current")
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
        """VM을 시작한다.

        파일 이벤트/폴링/웹/MQTT 등 여러 경로에서 동시에 호출될 수 있으므로,
        락과 쿨다운 그리고 시작 직전 실제 상태 확인으로 `POST status/start` 중복 전송을 막는다.
        이미 시작 명령이 전달된 상태라면 True를 반환한다(호출자 입장에서는 시작이 보장된 상태).
        """
        with self._vm_action_lock:
            elapsed = time.monotonic() - self._last_start_attempt
            if self._last_start_attempt > 0 and elapsed < self._start_cooldown:
                logging.info(
                    f"VM 시작 명령을 {elapsed:.1f}초 전에 이미 전송했습니다. "
                    f"중복 start 요청을 생략합니다. (쿨다운 {self._start_cooldown:.0f}초)")
                return True

            # 캐시된 상태는 최대 2초(또는 그 이상) 오래되었을 수 있으므로 실제 상태를 강제 조회한다.
            # 상태 조회가 실패하더라도 필요한 시작을 막지 않고 start 요청은 전송한다.
            try:
                data = self._get_vm_status_data(force_refresh=True)
                if data.get("status") == "running":
                    logging.info("VM이 이미 실행 중입니다. 중복 start 요청을 생략합니다.")
                    return True
            except Exception as e:
                logging.warning(f"VM 시작 전 상태 확인 실패(무시하고 start 요청 전송): {e}")

            try:
                self._request("POST", "status/start")
                self._last_start_attempt = time.monotonic()
                self._invalidate_status_cache()
                logging.debug("VM 시작 응답 받음")
                return True
            except Exception as e:
                logging.error(f"VM 시작 API 호출 실패: {e}")
                return False

    def stop_vm(self) -> bool:
        with self._vm_action_lock:
            # stop 직후 이어지는 start(예: reboot_vm)가 이전 start 쿨다운에 막히지 않도록 초기화한다.
            self._last_start_attempt = 0.0
            self._invalidate_status_cache()
            try:
                self._request("POST", "status/stop")
                self._invalidate_status_cache()
                logging.debug("VM 중지 응답 받음")
                return True
            except Exception as e:
                logging.error(f"VM 중지 API 호출 실패: {e}")
                return False