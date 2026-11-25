"""Proxmox VM start 중복 전송 방지 테스트.

여러 경로(nas-event-relay 파일 이벤트, 폴링 루프, SMB 최근 파일 링크 생성,
MQTT android_vm_on, 웹 /start_vm)에서 동시에 start_vm()이 호출되어도
Proxmox API로 `POST status/start`가 한 번만 나가는지 검증한다.
"""
import os
import sys
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app'))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def _ensure_optional_dependencies() -> None:
    """requests/urllib3/yaml 이 설치되지 않은 환경에서도 테스트가 돌도록 최소 스텁을 넣는다."""
    try:
        import requests  # noqa: F401
    except ModuleNotFoundError:
        requests_module = types.ModuleType("requests")

        class _Response:
            pass

        class _Session:
            def __init__(self):
                self.headers = {}
                self.verify = True
                self.mounted = {}

            def mount(self, prefix, adapter):
                self.mounted[prefix] = adapter

            def request(self, **kwargs):
                raise RuntimeError("스텁 세션은 실제 요청을 보내지 않습니다.")

        requests_module.Response = _Response
        requests_module.Session = _Session
        adapters_module = types.ModuleType("requests.adapters")

        class _HTTPAdapter:
            def __init__(self, max_retries=None, **kwargs):
                self.max_retries = max_retries

        adapters_module.HTTPAdapter = _HTTPAdapter
        requests_module.adapters = adapters_module
        sys.modules["requests"] = requests_module
        sys.modules["requests.adapters"] = adapters_module

    try:
        import urllib3  # noqa: F401
    except ModuleNotFoundError:
        urllib3_module = types.ModuleType("urllib3")
        util_module = types.ModuleType("urllib3.util")
        retry_module = types.ModuleType("urllib3.util.retry")

        class _Retry:
            def __init__(self, total=3, backoff_factor=0,
                         status_forcelist=None, allowed_methods=None, **kwargs):
                self.total = total
                self.allowed_methods = allowed_methods

        retry_module.Retry = _Retry
        util_module.retry = retry_module
        urllib3_module.util = util_module
        sys.modules["urllib3"] = urllib3_module
        sys.modules["urllib3.util"] = util_module
        sys.modules["urllib3.util.retry"] = retry_module

    try:
        import yaml  # noqa: F401
    except ModuleNotFoundError:
        yaml_module = types.ModuleType("yaml")
        yaml_module.safe_load = lambda *args, **kwargs: {}
        sys.modules["yaml"] = yaml_module


_ensure_optional_dependencies()

from proxmox_api import ProxmoxAPI  # noqa: E402


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return {"data": dict(self._data)}

    def raise_for_status(self):
        pass


class _FakeProxmox:
    """ProxmoxAPI._request 대신 사용하는 가짜 Proxmox API.

    실제 Proxmox처럼 `POST status/start`는 즉시 응답하고 status/current의
    상태는 곧바로 "running"으로 바뀌지 않는 상황을 재현한다.
    """

    def __init__(self, status: str = "stopped", post_delay: float = 0.0):
        self.vm_status = status
        self.post_delay = post_delay
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, method: str, endpoint: str):
        with self._lock:
            self.calls.append((method, endpoint))

        if method == "POST" and endpoint == "status/stop":
            self.vm_status = "stopped"
        if method == "POST" and endpoint == "status/start" and self.post_delay:
            time.sleep(self.post_delay)

        with self._lock:
            return _FakeResponse({
                "status": self.vm_status,
                "uptime": 0.0,
                "cpu": 0.0,
            })

    def count(self, method: str, endpoint: str) -> int:
        with self._lock:
            return sum(1 for c in self.calls if c == (method, endpoint))


def _make_config() -> MagicMock:
    config = MagicMock()
    config.TOKEN_ID = "root@pam!gshare"
    config.SECRET = "secret"
    config.PROXMOX_HOST = "https://pve.test:8006/api2/json"
    config.NODE_NAME = "pve"
    config.VM_ID = "100"
    config.PROXMOX_TIMEOUT = 10
    return config


def _make_api(fake: _FakeProxmox, cooldown: float = 15.0) -> ProxmoxAPI:
    api = ProxmoxAPI(_make_config())
    api._start_cooldown = cooldown
    api._request = fake  # type: ignore[assignment]
    return api


class TestConcurrentStartDedup(unittest.TestCase):
    def test_concurrent_starts_issue_single_post(self):
        """8개 스레드가 동시에 start_vm()을 호출해도 POST status/start는 1회."""
        fake = _FakeProxmox(status="stopped", post_delay=0.05)
        api = _make_api(fake)
        thread_count = 8
        barrier = threading.Barrier(thread_count)
        results = []
        errors = []
        results_lock = threading.Lock()

        def worker():
            try:
                barrier.wait(timeout=5)
                ok = api.start_vm()
                with results_lock:
                    results.append(ok)
            except Exception as exc:  # pragma: no cover - 실패 시 진단용
                with results_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), thread_count)
        self.assertTrue(all(results))
        self.assertEqual(fake.count("POST", "status/start"), 1)


class TestSequentialStartDedup(unittest.TestCase):
    def test_starts_within_cooldown_issue_single_post(self):
        """쿨다운(15초) 안에 연달아 호출하면 POST status/start는 1회."""
        fake = _FakeProxmox(status="stopped")
        api = _make_api(fake)

        self.assertTrue(api.start_vm())
        self.assertTrue(api.start_vm())
        self.assertTrue(api.start_vm())

        self.assertEqual(fake.count("POST", "status/start"), 1)

    def test_start_after_cooldown_issued_again(self):
        """쿨다운이 지난 뒤의 start 요청은 다시 전송된다."""
        fake = _FakeProxmox(status="stopped")
        api = _make_api(fake)

        self.assertTrue(api.start_vm())
        api._last_start_attempt = time.monotonic() - (api._start_cooldown + 1)
        self.assertTrue(api.start_vm())

        self.assertEqual(fake.count("POST", "status/start"), 2)


class TestStartWhenAlreadyRunning(unittest.TestCase):
    def test_no_post_when_vm_already_running(self):
        """status/current가 running이면 POST status/start를 보내지 않는다."""
        fake = _FakeProxmox(status="running")
        api = _make_api(fake)

        self.assertTrue(api.start_vm())

        self.assertEqual(fake.count("POST", "status/start"), 0)
        self.assertEqual(fake.count("GET", "status/current"), 1)

    def test_no_post_when_cached_status_is_stale_stopped(self):
        """2초 캐시에 stopped가 남아 있어도 실제 상태(running)를 확인해 생략한다."""
        fake = _FakeProxmox(status="stopped")
        api = _make_api(fake)

        # 캐시를 stopped 상태로 채운다.
        self.assertFalse(api.is_vm_running())
        self.assertIsNotNone(api._cached_status)

        # 캐시 TTL 안에 VM이 실행되었고 다른 경로에서 start가 호출된 상황.
        fake.vm_status = "running"
        self.assertTrue(api.start_vm())

        self.assertEqual(fake.count("POST", "status/start"), 0)

    def test_status_check_failure_does_not_block_start(self):
        """상태 조회가 실패해도 start는 막지 않고 전송한다."""
        fake = _FakeProxmox(status="stopped")
        api = _make_api(fake)
        original = fake.__call__

        def failing_get(method, endpoint):
            if method == "GET":
                raise RuntimeError("status API 일시 오류")
            return original(method, endpoint)

        api._request = failing_get  # type: ignore[assignment]

        self.assertTrue(api.start_vm())
        self.assertEqual(fake.count("POST", "status/start"), 1)


class TestStopResetsCooldown(unittest.TestCase):
    def test_stop_then_start_works_immediately(self):
        """stop_vm()은 쿨다운을 초기화해 reboot_vm()의 stop→start가 막히지 않는다."""
        fake = _FakeProxmox(status="stopped")
        api = _make_api(fake)

        self.assertTrue(api.start_vm())
        self.assertEqual(fake.count("POST", "status/start"), 1)

        fake.vm_status = "running"
        self.assertTrue(api.stop_vm())
        self.assertEqual(api._last_start_attempt, 0.0)

        fake.vm_status = "stopped"
        self.assertTrue(api.start_vm())

        self.assertEqual(fake.count("POST", "status/stop"), 1)
        self.assertEqual(fake.count("POST", "status/start"), 2)


class TestStatusCacheInvalidation(unittest.TestCase):
    def test_start_vm_invalidates_status_cache(self):
        fake = _FakeProxmox(status="stopped")
        api = _make_api(fake)

        api._get_vm_status_data()
        self.assertIsNotNone(api._cached_status)

        self.assertTrue(api.start_vm())

        self.assertIsNone(api._cached_status)
        self.assertEqual(api._last_status_check, 0.0)

    def test_stop_vm_invalidates_status_cache(self):
        fake = _FakeProxmox(status="running")
        api = _make_api(fake)

        self.assertTrue(api.is_vm_running())
        self.assertIsNotNone(api._cached_status)

        self.assertTrue(api.stop_vm())

        self.assertIsNone(api._cached_status)
        self.assertEqual(api._last_status_check, 0.0)

    def test_force_refresh_bypasses_cache(self):
        fake = _FakeProxmox(status="stopped")
        api = _make_api(fake)

        api._get_vm_status_data()
        fake.vm_status = "running"

        # 캐시가 유효한 시점에도 force_refresh는 실제 상태를 다시 읽는다.
        data = api._get_vm_status_data(force_refresh=True)
        self.assertEqual(data["status"], "running")
        self.assertEqual(fake.count("GET", "status/current"), 2)


class TestRetryPolicy(unittest.TestCase):
    def test_post_is_not_retried(self):
        """POST(status/start·status/stop)는 자동 재시도 대상에서 제외한다."""
        with patch("proxmox_api.Retry") as retry_cls, \
                patch("proxmox_api.HTTPAdapter"):
            ProxmoxAPI(_make_config())

        self.assertTrue(retry_cls.called)
        allowed_methods = retry_cls.call_args.kwargs["allowed_methods"]
        self.assertNotIn("POST", allowed_methods)


if __name__ == "__main__":
    unittest.main()
