import logging
import json
from flask import Flask, jsonify, render_template, request, redirect, url_for  # type: ignore
import threading
import subprocess
import os
import re
import sys
import requests  # type: ignore
from datetime import datetime
import pytz  # type: ignore
import yaml  # type: ignore
import socket
import tempfile
from config import (GshareConfig, CONFIG_PATH, INIT_FLAG_PATH,
                    RESTART_FLAG_PATH, LOG_FILE_PATH)  # type: ignore
import time
import traceback
from flask_socketio import SocketIO  # type: ignore


class GshareWebServer:
    """GShare 웹 서버를 관리하는 클래스"""

    def __init__(self):
        """웹 서버 초기화"""
        self.app = Flask(__name__)
        # Flask 로그 비활성화
        cli = sys.modules.get('flask.cli')
        if cli:
            cli.show_server_banner = lambda *x: None  # type: ignore

        self.app.logger.disabled = True
        log = logging.getLogger('werkzeug')
        log.disabled = True
        self.manager = None
        self.config = None
        self.is_setup_complete = False
        self.log_file = LOG_FILE_PATH
        # SocketIO 초기화
        self.socketio = SocketIO(self.app, cors_allowed_origins="*")
        self._setup_logging()
        self._register_routes()
        
        # 소켓 상태 업데이트를 위한 변수들
        self.active_connections = 0
        self.state_update_timer = None
        self.timer_lock = threading.Lock()

    def set_manager(self, manager):
        self.manager = manager

    def set_config(self, config):
        self.config = config

    def _get_app_version(self):
        """pyproject.toml에서 앱 버전 읽기"""
        try:
            # Docker 환경에서는 /app/pyproject.toml, 로컬 개발 환경에서는 상위 디렉토리 등 확인
            possible_paths = [
                'pyproject.toml',
                '../pyproject.toml',
                '/app/pyproject.toml'
            ]

            for path in possible_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        toml_content = f.read()
                        # version = "1.3.3" 형태 찾기
                        match = re.search(r'version\s*=\s*"([^"]+)"', toml_content)
                        if match:
                            return match.group(1)

            logging.warning("pyproject.toml을 찾을 수 없거나 버전을 읽을 수 없습니다.")
            return "Unknown"
        except Exception as e:
            logging.error(f"앱 버전 읽기 실패: {e}")
            return "Unknown"

    def _is_nfs_mount_present(self, mount_path: str, nfs_path: str = None) -> bool:
        """/proc/mounts 기준으로 nfs/nfs4 마운트 여부를 확인한다."""
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
            logging.debug(f"/proc/mounts 기반 NFS 확인 실패: {e}")

        return False

    def _setup_logging(self):
        """로깅 설정"""
        # 프로덕션 모드에서는 Flask 로그 비활성화
        cli = sys.modules.get('flask.cli')
        if cli:
            cli.show_server_banner = lambda *x: None  # type: ignore
        self.app.logger.disabled = True
        log = logging.getLogger('werkzeug')
        log.disabled = True

    def init_server(self):
        """웹서버 초기화"""
        try:
            logging.debug("웹 서버 상태 초기화 시작")
            self.is_setup_complete = True

            # manager 객체 상태 확인
            if self.manager:
                logging.debug(
                    f"manager.current_state 존재: {hasattr(self.manager, 'current_state') and self.manager.current_state is not None}")
                if hasattr(self.manager, 'current_state') and self.manager.current_state is None:
                    logging.warning(
                        "manager.current_state가 None 값입니다. 초기화가 필요할 수 있습니다.")

            restart_flag_path = RESTART_FLAG_PATH
            if os.path.exists(restart_flag_path):
                logging.debug("이전 재시작 플래그 파일이 발견되었습니다. 삭제합니다.")
                try:
                    os.remove(restart_flag_path)
                except Exception as e:
                    logging.error(f"재시작 플래그 파일 삭제 중 오류: {e}")

            logging.debug("웹 서버 상태 초기화 완료")
        except Exception as e:
            logging.error(f"웹 서버 초기화 중 오류 발생: {e}")
            logging.error(f"상세 오류: {traceback.format_exc()}")

            # 오류가 발생해도 기본 설정은 완료하도록 함
            self.is_setup_complete = False
            logging.warning("웹 서버 초기화 실패로 설정 페이지로 리디렉션됩니다.")

    def _get_default_state(self):
        """State가 초기화되지 않았을 때 사용할 기본값을 반환"""
        from main import State

        current_time = datetime.now(tz=pytz.timezone(
            'Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
        return State(
            last_check_time=current_time,
            vm_running=False,
            cpu_usage=0.0,
            cpu_threshold=0.0,
            last_action="초기화되지 않음",
            low_cpu_count=0,
            threshold_count=0,
            uptime="알 수 없음",
            last_shutdown_time="-",
            monitored_folders={},
            smb_running=False,
            check_interval=60,
            monitor_mode='event'
        )

    def _get_container_ip(self):
        """도커 컨테이너의 IP 주소를 가져옵니다."""
        try:
            hostname = socket.gethostname()
            ip_address = socket.gethostbyname(hostname)
            return ip_address
        except Exception as e:
            logging.error(f"IP 주소 가져오기 실패: {e}")
            return "알 수 없음"

    def _register_routes(self):
        """Flask 라우트 등록"""
        self.app.add_url_rule('/', 'main_page', self.main_page)
        self.app.add_url_rule('/setup', 'setup', self.setup)
        self.app.add_url_rule('/save-config', 'save_config',
                              self.save_config, methods=['POST'])
        self.app.add_url_rule('/settings', 'show_settings', self.show_settings)
        self.app.add_url_rule(
            '/update_state', 'update_state', self.update_state)
        self.app.add_url_rule('/update_log', 'update_log', self.update_log)
        self.app.add_url_rule('/clear_log', 'clear_log', self.clear_log)
        self.app.add_url_rule('/trim_log/<int:lines>',
                              'trim_log', self.trim_log)
        self.app.add_url_rule('/set_log_level/<string:level>',
                              'set_log_level', self.set_log_level)
        self.app.add_url_rule(
            '/get_log_level', 'get_log_level', self.get_log_level)
        self.app.add_url_rule('/start_vm', 'start_vm', self.start_vm)
        self.app.add_url_rule('/shutdown_vm', 'shutdown_vm', self.shutdown_vm)
        self.app.add_url_rule('/toggle_mount/<path:folder>',
                              'toggle_mount', self.toggle_mount)
        # SMB 토글 엔드포인트를 두 개로 분리
        self.app.add_url_rule('/activate_smb', 'activate_smb', self.activate_smb)
        self.app.add_url_rule('/deactivate_smb', 'deactivate_smb', self.deactivate_smb)
        self.app.add_url_rule('/remount_nfs', 'remount_nfs', self.remount_nfs)
        self.app.add_url_rule('/get_config', 'get_config', self.get_config)
        self.app.add_url_rule('/update_config', 'update_config',
                              self.update_config, methods=['POST'])
        self.app.add_url_rule('/api/folder-event', 'folder_event',
                              self.folder_event, methods=['POST'])
        self.app.add_url_rule(
            '/test_proxmox_api', 'test_proxmox_api', self.test_proxmox_api, methods=['POST'])
        self.app.add_url_rule('/test_nfs', 'test_nfs',
                              self.test_nfs, methods=['POST'])
        self.app.add_url_rule('/test_mqtt', 'test_mqtt',
                              self.test_mqtt, methods=['POST'])
        self.app.add_url_rule('/import-config', 'import_config',
                              self.import_config, methods=['POST'])
        self.app.add_url_rule(
            '/export-config', 'export_config', self.export_config)
        self.app.add_url_rule('/restart_app', 'restart_app',
                              self.restart_app, methods=['POST'])
        self.app.add_url_rule('/check_restart_status',
                              'check_restart_status', self.check_restart_status)
        self.app.add_url_rule('/get_transcoding_config',
                              'get_transcoding_config', self.get_transcoding_config)
        self.app.add_url_rule('/update_transcoding_config',
                              'update_transcoding_config', self.update_transcoding_config, methods=['POST'])
        self.app.add_url_rule('/scan_transcoding',
                              'scan_transcoding', self.scan_transcoding, methods=['POST'])
        self.app.add_url_rule('/cancel_transcoding_scan',
                              'cancel_transcoding_scan', self.cancel_transcoding_scan, methods=['POST'])
        self.app.add_url_rule('/get_scan_status',
                              'get_scan_status', self.get_scan_status)

        # SocketIO 이벤트 핸들러 등록
        self._register_socket_events()

        # 에러 핸들러 등록
        @self.app.errorhandler(Exception)
        def handle_exception(e):
            # 모든 예외를 로깅
            logging.error(f"에러 발생: {str(e)}")
            logging.error(traceback.format_exc())

            # 개발 중에는 디버그 정보를 포함하는 에러 페이지 반환
            if os.getenv('FLASK_ENV') == 'development' or os.getenv('LOG_LEVEL') == 'DEBUG':
                return f"""
                <h1>서버 에러가 발생했습니다</h1>
                <p>죄송합니다. 요청을 처리하는 동안 오류가 발생했습니다.</p>
                <h2>에러 상세 정보:</h2>
                <pre>{str(e)}</pre>
                <h3>스택 트레이스:</h3>
                <pre>{traceback.format_exc()}</pre>
                """, 500
            else:
                # 프로덕션 환경에서는 간단한 에러 메시지만 표시
                return """
                <h1>서버 에러가 발생했습니다</h1>
                <p>죄송합니다. 요청을 처리하는 동안 오류가 발생했습니다.</p>
                <p>로그를 확인하거나 관리자에게 문의하세요.</p>
                """, 500

        @self.app.errorhandler(404)
        def page_not_found(e):
            return """
            <h1>페이지를 찾을 수 없습니다</h1>
            <p>요청하신 페이지가 존재하지 않습니다.</p>
            <p><a href="/">메인 페이지로 돌아가기</a></p>
            """, 404

    def _register_socket_events(self):
        """SocketIO 이벤트 핸들러 등록"""
        @self.socketio.on('connect')
        def handle_connect():
            logging.debug("클라이언트가 WebSocket에 연결되었습니다.")
            # 연결 시 즉시 현재 상태 전송
            self.emit_state_update()
            self.emit_log_update()
            
            # 활성 연결 수 증가 및 상태 업데이트 타이머 시작
            with self.timer_lock:
                self.active_connections += 1
                if self.active_connections == 1 and self.state_update_timer is None:
                    self._start_state_update_timer()

        @self.socketio.on('request_state')
        def handle_request_state():
            logging.debug("클라이언트가 상태 정보를 요청했습니다.")
            self.emit_state_update()

        @self.socketio.on('request_log')
        def handle_request_log():
            logging.debug("클라이언트가 로그 정보를 요청했습니다.")
            self.emit_log_update()
            
        @self.socketio.on('disconnect')
        def handle_disconnect():
            logging.debug("클라이언트가 WebSocket 연결을 종료했습니다.")
            # 활성 연결 수 감소 및 필요 시 타이머 중지
            with self.timer_lock:
                self.active_connections = max(0, self.active_connections - 1)
                if self.active_connections == 0 and self.state_update_timer is not None:
                    self._stop_state_update_timer()

    def main_page(self):
        """메인 페이지 표시"""
        try:
            version = self._get_app_version()
            if not self.is_setup_complete:
                logging.debug("'/' 경로 접근: 설정 페이지로 리디렉션")
                return redirect(url_for('setup'))

            if self.manager:
                try:
                    current_state = self.manager.current_state
                    return render_template('index.html', state=current_state, config=self.config, version=version)
                except Exception as e:
                    logging.error(f"manager.current_state 사용 중 에러: {e}")
                    logging.error(traceback.format_exc())
                    return render_template('index.html', state=self._get_default_state(), config=self.config, version=version)
            else:
                logging.info("manager가 없음, 기본 상태 사용")
                default_state = self._get_default_state()
                logging.info(f"기본 상태 생성 완료: {default_state is not None}")
                return render_template('index.html', state=default_state, config=None, version=version)
        except Exception as e:
            logging.error(f"메인 페이지 렌더링 중 에러 발생: {e}")
            logging.error(traceback.format_exc())
            return f"<h1>에러가 발생했습니다</h1><p>{str(e)}</p><pre>{traceback.format_exc()}</pre>", 500

    def _config_to_form_data(self, yaml_config, mask_secrets=False):
        """설정 데이터를 폼 데이터로 변환"""
        creds = yaml_config.get('credentials', {})
        proxmox = yaml_config.get('proxmox', {})
        proxmox_cpu = proxmox.get('cpu', {})
        mount = yaml_config.get('mount', {})
        nfs = yaml_config.get('nfs', {})
        smb = yaml_config.get('smb', {})
        mqtt = yaml_config.get('mqtt', {})
        transcoding = yaml_config.get('transcoding', {})

        form_data = {
            'PROXMOX_HOST': creds.get('proxmox_host', ''),
            'NODE_NAME': proxmox.get('node_name', ''),
            'VM_ID': proxmox.get('vm_id', ''),
            'PROXMOX_TIMEOUT': proxmox.get('timeout', 5),
            'CPU_THRESHOLD': proxmox_cpu.get('threshold', 10),
            'CHECK_INTERVAL': proxmox_cpu.get('check_interval', 60),
            'THRESHOLD_COUNT': proxmox_cpu.get('threshold_count', 3),
            'MOUNT_PATH': mount.get('path', '/mnt/gshare'),
            'GET_FOLDER_SIZE_TIMEOUT': mount.get('folder_size_timeout', 30),
            'NFS_PATH': nfs.get('path', ''),
            'SHUTDOWN_WEBHOOK_URL': creds.get('shutdown_webhook_url', ''),
            'SMB_USERNAME': creds.get('smb_username', ''),
            'SMB_PASSWORD': creds.get('smb_password', ''),
            'SMB_SHARE_NAME': smb.get('share_name', 'gshare'),
            'SMB_COMMENT': smb.get('comment', 'GShare SMB 공유'),
            'SMB_GUEST_OK': 'yes' if smb.get('guest_ok', False) else 'no',
            'SMB_READ_ONLY': 'yes' if smb.get('read_only', True) else 'no',
            'SMB_LINKS_DIR': smb.get('links_dir', '/mnt/gshare_links'),
            'SMB_PORT': smb.get('port', 445),
            'TIMEZONE': yaml_config.get('timezone', 'Asia/Seoul'),
            'LOG_LEVEL': yaml_config.get('log_level', 'INFO'),
            'MQTT_BROKER': mqtt.get('broker', ''),
            'MQTT_PORT': mqtt.get('port', 1883),
            'MQTT_USERNAME': creds.get('mqtt_username', ''),
            'MQTT_PASSWORD': creds.get('mqtt_password', ''),
            'MQTT_TOPIC_PREFIX': mqtt.get('topic_prefix', 'gshare'),
            'HA_DISCOVERY_PREFIX': mqtt.get('ha_discovery_prefix', 'homeassistant'),
            'MONITOR_MODE': yaml_config.get('monitoring', {}).get('mode', 'event'),
            'EVENT_AUTH_TOKEN': creds.get('event_auth_token', ''),
            'TOKEN_ID': creds.get('token_id', ''),
            'SECRET': creds.get('secret', ''),
            'TRANSCODING_ENABLED': transcoding.get('enabled', False),
            'TRANSCODING_RULES': transcoding.get('rules', [])
        }

        if mask_secrets:
            if form_data['SECRET']:
                form_data['SECRET'] = '********'
            if form_data['SMB_PASSWORD']:
                form_data['SMB_PASSWORD'] = '********'
            if form_data['MQTT_PASSWORD']:
                form_data['MQTT_PASSWORD'] = '********'
            if form_data['EVENT_AUTH_TOKEN']:
                form_data['EVENT_AUTH_TOKEN'] = '********'

        return form_data

    def setup(self):
        """초기 설정 페이지"""
        container_ip = self._get_container_ip()
        config_path = CONFIG_PATH
        form_data = {}
        has_config = False

        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    yaml_config = yaml.safe_load(f)
                    has_config = True

                form_data = self._config_to_form_data(yaml_config, mask_secrets=False)
                logging.debug("기존 설정 파일 로드 성공")
            except Exception as e:
                logging.error(f"설정 파일 로드 중 오류 발생: {e}")

        logging.debug("랜딩 페이지 표시")
        return render_template('landing.html', form_data=form_data, has_config=has_config, container_ip=container_ip)

    def save_config(self):
        """설정 저장"""
        config_path = CONFIG_PATH
        init_flag_path = INIT_FLAG_PATH

        logging.debug("설정 저장 시작")

        try:
            form_data = request.form.to_dict()

            transcoding_enabled_raw = str(form_data.get('TRANSCODING_ENABLED', 'false')).strip().lower()
            transcoding_enabled = transcoding_enabled_raw in ('true', '1', 'yes', 'on')
            transcoding_rules_raw = form_data.get('TRANSCODING_RULES', '[]')
            try:
                transcoding_rules = json.loads(transcoding_rules_raw) if transcoding_rules_raw else []
                if not isinstance(transcoding_rules, list):
                    transcoding_rules = []
            except json.JSONDecodeError:
                logging.warning('TRANSCODING_RULES JSON 파싱 실패, 빈 목록으로 저장합니다.')
                transcoding_rules = []

            config_dict = {
                'PROXMOX_HOST': form_data.get('PROXMOX_HOST', ''),
                'NODE_NAME': form_data.get('NODE_NAME', ''),
                'VM_ID': form_data.get('VM_ID', ''),
                'PROXMOX_TIMEOUT': form_data.get('PROXMOX_TIMEOUT', 5),
                'TOKEN_ID': form_data.get('TOKEN_ID', ''),
                'SECRET': form_data.get('SECRET', ''),
                'CPU_THRESHOLD': form_data.get('CPU_THRESHOLD', 10),
                'CHECK_INTERVAL': form_data.get('CHECK_INTERVAL', 60),
                'THRESHOLD_COUNT': form_data.get('THRESHOLD_COUNT', 3),
                'MOUNT_PATH': form_data.get('MOUNT_PATH', ''),
                'GET_FOLDER_SIZE_TIMEOUT': form_data.get('GET_FOLDER_SIZE_TIMEOUT', 30),
                'NFS_PATH': form_data.get('NFS_PATH', ''),
                'SHUTDOWN_WEBHOOK_URL': form_data.get('SHUTDOWN_WEBHOOK_URL', ''),
                'SMB_USERNAME': form_data.get('SMB_USERNAME', ''),
                'SMB_PASSWORD': form_data.get('SMB_PASSWORD', ''),
                'SMB_SHARE_NAME': form_data.get('SMB_SHARE_NAME', 'gshare'),
                'SMB_COMMENT': form_data.get('SMB_COMMENT', 'GShare SMB 공유'),
                'SMB_GUEST_OK': form_data.get('SMB_GUEST_OK', 'no'),
                'SMB_READ_ONLY': form_data.get('SMB_READ_ONLY', 'yes'),
                'SMB_LINKS_DIR': form_data.get('SMB_LINKS_DIR', '/mnt/gshare_links'),
                'TIMEZONE': form_data.get('TIMEZONE', 'Asia/Seoul'),
                'LOG_LEVEL': form_data.get('LOG_LEVEL', 'INFO'),
                'MQTT_BROKER': form_data.get('MQTT_BROKER', ''),
                'MQTT_PORT': form_data.get('MQTT_PORT', 1883),
                'MQTT_USERNAME': form_data.get('MQTT_USERNAME', ''),
                'MQTT_PASSWORD': form_data.get('MQTT_PASSWORD', ''),
                'MQTT_TOPIC_PREFIX': form_data.get('MQTT_TOPIC_PREFIX', 'gshare'),
                'HA_DISCOVERY_PREFIX': form_data.get('HA_DISCOVERY_PREFIX', 'homeassistant'),
                'MONITOR_MODE': form_data.get('MONITOR_MODE', 'event'),
                'EVENT_AUTH_TOKEN': form_data.get('EVENT_AUTH_TOKEN', ''),
                'TRANSCODING_ENABLED': transcoding_enabled,
                'TRANSCODING_RULES': transcoding_rules,
                'SAVE_TIME': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }

            GshareConfig.update_yaml_config(config_dict)

            try:
                config_mtime = os.path.getmtime(config_path)
                config_time = datetime.fromtimestamp(config_mtime)
                logging.debug(
                    f"설정 파일 업데이트 시간: {config_time.strftime('%Y-%m-%d %H:%M:%S')}")

                time.sleep(1)

                current_time = datetime.now()
                with open(init_flag_path, 'w') as f:
                    time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
                    f.write(time_str)
                logging.debug(f"초기화 완료 플래그 생성 시간: {time_str}")

                time_diff = (current_time - config_time).total_seconds()
                logging.debug(f"설정 파일과 초기화 플래그 사이 시간 차이: {time_diff:.2f}초")

                if time_diff < 0:
                    logging.warning(
                        "초기화 플래그가 설정 파일보다 이른 시간으로 설정되었습니다. 문제가 발생할 수 있습니다.")
            except Exception as e:
                logging.error(f"파일 시간 설정 중 오류 발생: {e}")
                with open(init_flag_path, 'w') as f:
                    f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

            logging.debug(f"설정 저장 완료 - 설정 완료 페이지로 이동합니다.")
            return render_template('setup_complete.html')

        except Exception as e:
            logging.error(f"설정 저장 중 오류 발생: {e}")
            return render_template('landing.html', error=str(e), form_data=request.form)

    def folder_event(self):
        """NAS에서 전달한 폴더 이벤트를 처리"""
        try:
            if self.manager is None:
                return jsonify({"status": "error", "message": "서버가 아직 초기화되지 않았습니다."}), 503

            payload = request.get_json(silent=True) or {}
            folder = (payload.get('folder') or payload.get('folder_path') or '').strip()
            token = request.headers.get('X-GShare-Token', '')
            if not token:
                token = (payload.get('token') or '').strip()

            expected_token = (getattr(self.config, 'EVENT_AUTH_TOKEN', '') or '').strip() if self.config else ''
            if expected_token and token != expected_token:
                return jsonify({"status": "error", "message": "인증 실패"}), 401

            if not folder:
                return jsonify({"status": "error", "message": "folder 필드가 필요합니다."}), 400

            success, detail = self.manager.handle_folder_event(folder)
            if not success:
                return jsonify({"status": "error", "message": f"이벤트 처리 실패: {detail}"}), 500

            self.manager.current_state = self.manager.update_state(update_monitored_folders=True)
            self.emit_state_update()
            if self.manager.mqtt_manager:
                self.manager.mqtt_manager.publish_state(self.manager.current_state)

            return jsonify({"status": "success", "message": "이벤트 처리 완료", "mounted": detail})
        except Exception as e:
            logging.error(f"폴더 이벤트 API 오류: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    def show_settings(self):
        return redirect(url_for('setup'))

    def update_state(self):
        """상태 업데이트"""
        try:
            if self.manager is not None and hasattr(self.manager, 'current_state'):
                if self.manager.current_state:
                    return jsonify(self.manager.current_state.to_dict())
                else:
                    state = self._get_default_state()
                    logging.debug(
                        f"gshare_manager의 current_state가 None, 기본 상태 사용 - last_check_time: {state.last_check_time}")
                    return jsonify(state.to_dict())
            else:
                state = self._get_default_state()
                logging.debug(
                    f"gshare_manager 객체 없음, 기본 상태 사용 - last_check_time: {state.last_check_time}")
                return jsonify(state.to_dict())
        except Exception as e:
            logging.error(f"상태 요청 중 오류: {e}")
            state = self._get_default_state()
            return jsonify(state.to_dict())

    def emit_state_update(self):
        """소켓을 통해 상태 업데이트 전송"""
        try:
            if self.manager is not None and hasattr(self.manager, 'current_state'):
                if self.manager.current_state:
                    state_dict = self.manager.current_state.to_dict()
                    self.socketio.emit('state_update', state_dict)
                    logging.debug(f"소켓을 통한 상태 업데이트 전송 - last_check_time: {state_dict.get('last_check_time')}")
                    return True
            return False
        except Exception as e:
            logging.error(f"소켓 상태 전송 중 오류: {e}")
            return False

    def update_log(self):
        """로그 업데이트"""
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r', encoding='utf-8', errors='replace') as file:
                    log_content = file.read()
                    return log_content
            except Exception as e:
                logging.error(f"로그 파일 읽기 중 오류: {e}")
                return f"Error reading log file: {str(e)}"
        else:
            return "Log file not found.", 404

    def emit_log_update(self):
        """소켓을 통해 로그 업데이트 전송"""
        try:
            if os.path.exists(self.log_file):
                log_content = ""
                # Limit read size to avoid memory issues and improve performance
                max_read_size = 100 * 1024  # 100KB (approx 1000 lines)

                try:
                    file_size = os.path.getsize(self.log_file)
                except OSError:
                    file_size = 0

                if file_size > max_read_size:
                    with open(self.log_file, 'rb') as file:
                        file.seek(-max_read_size, 2)
                        raw_content = file.read()
                        # Decode and discard partial first line if necessary
                        decoded_content = raw_content.decode('utf-8', errors='replace')
                        if '\n' in decoded_content:
                            log_content = decoded_content.split('\n', 1)[1]
                        else:
                            log_content = decoded_content
                else:
                    with open(self.log_file, 'r', encoding='utf-8', errors='replace') as file:
                        log_content = file.read()

                self.socketio.emit('log_update', log_content)
                logging.debug("소켓을 통한 로그 업데이트 전송")
                return True
            return False
        except Exception as e:
            logging.error(f"소켓 로그 전송 중 오류: {e}")
            return False

    def clear_log(self):
        """로그 지우기"""
        try:
            with open(self.log_file, 'w') as file:
                file.truncate(0)
            return jsonify({"status": "success", "message": "로그가 성공적으로 삭제되었습니다."})
        except Exception as e:
            return jsonify({"status": "error", "message": f"로그 삭제 실패: {str(e)}"}), 500

    def trim_log(self, lines):
        """로그 줄이기"""
        try:
            with open(self.log_file, 'r') as file:
                log_lines = file.readlines()

            trimmed_lines = log_lines[-lines:] if len(
                log_lines) > lines else log_lines

            with open(self.log_file, 'w') as file:
                file.writelines(trimmed_lines)

            return jsonify({
                "status": "success",
                "message": f"로그가 마지막 {lines}줄만 남도록 정리되었습니다.",
                "total_lines": len(trimmed_lines)
            })
        except Exception as e:
            return jsonify({"status": "error", "message": f"로그 정리 실패: {str(e)}"}), 500

    def set_log_level(self, level):
        """로그 레벨 설정"""
        try:
            valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

            if level.upper() not in valid_levels:
                return jsonify({
                    "status": "error",
                    "message": f"유효하지 않은 로그 레벨입니다. 가능한 레벨: {', '.join(valid_levels)}"
                }), 400

            numeric_level = getattr(logging, level.upper(), None)
            if not isinstance(numeric_level, int):
                return jsonify({"status": "error", "message": "유효하지 않은 로그 레벨입니다."}), 400

            os.environ['LOG_LEVEL'] = level.upper()

            logging.getLogger().setLevel(numeric_level)

            for logger_name in logging.root.manager.loggerDict:
                logging.getLogger(logger_name).setLevel(numeric_level)

            try:
                config_data = {
                    'LOG_LEVEL': level.upper()
                }
                GshareConfig.update_yaml_config(config_data)
            except Exception as e:
                logging.error(f"config.yaml에 로그 레벨 업데이트 실패: {str(e)}")

            return jsonify({
                "status": "success",
                "message": f"로그 레벨이 {level.upper()}로 변경되었습니다."
            })
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"로그 레벨 변경 실패: {str(e)}"
            }), 500

    def get_log_level(self):
        """로그 레벨 가져오기"""
        try:
            yaml_path = CONFIG_PATH

            if os.path.exists(yaml_path):
                with open(yaml_path, 'r', encoding='utf-8') as f:
                    yaml_config = yaml.safe_load(f)
                level = yaml_config.get('log_level', 'INFO')
            else:
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

    def start_vm(self):
        """VM 시작"""
        try:
            if self.manager is None:
                return jsonify({"status": "error", "message": "서버가 아직 초기화되지 않았습니다."}), 404

            if self.manager.current_state.vm_running:
                return jsonify({"status": "error", "message": "VM이 이미 실행 중입니다."}), 400

            if self.manager.proxmox_api.start_vm():
                return jsonify({"status": "success", "message": "VM 시작이 요청되었습니다."})
            else:
                return jsonify({"status": "error", "message": "VM 시작 실패"}), 500
        except Exception as e:
            return jsonify({"status": "error", "message": f"VM 시작 요청 실패: {str(e)}"}), 500

    def shutdown_vm(self):
        """VM 종료"""
        try:
            if self.manager is None or self.config is None:
                return jsonify({"status": "error", "message": "서버가 아직 초기화되지 않았습니다."}), 404

            if not self.manager.current_state.vm_running:
                return jsonify({"status": "error", "message": "VM이 이미 종료되어 있습니다."}), 400

            response = requests.post(
                self.config.SHUTDOWN_WEBHOOK_URL, timeout=5)
            response.raise_for_status()
            return jsonify({"status": "success", "message": "VM 종료가 요청되었습니다."})
        except Exception as e:
            return jsonify({"status": "error", "message": f"VM 종료 요청 실패: {str(e)}"}), 500

    def toggle_mount(self, folder):
        """마운트 토글"""
        try:
            if self.manager is None:
                return jsonify({"status": "error", "message": "서버가 아직 초기화되지 않았습니다."}), 404

            # 실제 심볼릭 링크가 존재하는지 확인
            links_dir = self.manager.smb_manager.links_dir
            symlink_path = os.path.join(links_dir, folder.replace(os.sep, '_'))
            is_mounted = os.path.exists(
                symlink_path) and os.path.islink(symlink_path)

            if is_mounted:
                # 마운트 해제
                if self.manager.smb_manager.remove_symlink(folder):
                    # 마운트 상태만 업데이트 (효율적인 상태 업데이트)
                    self.manager.update_folder_mount_state(folder, False)
                    self.emit_state_update()
                    return jsonify({"status": "success", "message": f"{folder} 마운트가 해제되었습니다."})
                else:
                    return jsonify({"status": "error", "message": f"{folder} 마운트 해제 실패"}), 500
            else:
                # 마운트 활성화
                if self.manager.smb_manager.create_symlink(folder) and self.manager.smb_manager.activate_smb_share():
                    # 마운트 상태만 업데이트 (효율적인 상태 업데이트)
                    self.manager.update_folder_mount_state(folder, True)
                    self.emit_state_update()
                    return jsonify({"status": "success", "message": f"{folder} 마운트가 활성화되었습니다."})
                else:
                    return jsonify({"status": "error", "message": f"{folder} 마운트 활성화 실패"}), 500
        except Exception as e:
            return jsonify({"status": "error", "message": f"마운트 상태 변경 실패: {str(e)}"}), 500

    def activate_smb(self):
        """SMB 활성화"""
        try:
            if self.manager is None:
                return jsonify({"status": "error", "message": "서버가 아직 초기화되지 않았습니다."}), 404
                
            # SMB 활성화
            if self.manager.smb_manager.activate_smb_share():
                return jsonify({"status": "success", "message": "SMB 공유가 활성화되었습니다."})
            else:
                return jsonify({"status": "error", "message": "SMB 공유 활성화 실패"}), 500
        except Exception as e:
            return jsonify({"status": "error", "message": f"SMB 활성화 실패: {str(e)}"}), 500

    def deactivate_smb(self):
        """SMB 비활성화"""
        try:
            if self.manager is None:
                return jsonify({"status": "error", "message": "서버가 아직 초기화되지 않았습니다."}), 404
                
            # SMB 비활성화
            if self.manager.smb_manager.deactivate_smb_share():
                return jsonify({"status": "success", "message": "SMB 공유가 비활성화되었습니다."})
            else:
                return jsonify({"status": "error", "message": "SMB 공유 비활성화 실패"}), 500
        except Exception as e:
            return jsonify({"status": "error", "message": f"SMB 비활성화 실패: {str(e)}"}), 500

    def remount_nfs(self):
        """NFS 재마운트"""
        try:
            if self.manager is None:
                return jsonify({"status": "error", "message": "서버가 아직 초기화되지 않았습니다."}), 404

            # NFS 재마운트 시도
            logging.info("NFS 재마운트 시도")
            
            # 마운트 경로 가져오기
            if not hasattr(self.manager, 'config') or not hasattr(self.manager.config, 'MOUNT_PATH'):
                return jsonify({"status": "error", "message": "NFS 마운트 경로 설정이 없습니다."}), 500
                
            mount_path = self.manager.config.MOUNT_PATH
            
            try:
                # 현재 마운트 상태 확인
                is_mounted = self._is_nfs_mount_present(mount_path, self.manager.config.NFS_PATH)
                        
                if is_mounted:
                    # NFS 마운트 해제 시도
                    logging.debug(f"기존 NFS 마운트 해제 시도: {mount_path}")
                    subprocess.run(["umount", "-f", mount_path], stderr=subprocess.PIPE, check=False)
                    logging.debug(f"NFS 마운트 해제 명령 실행 완료")
                else:
                    logging.debug(f"NFS가 마운트되어 있지 않음: {mount_path}")
            except Exception as e:
                logging.warning(f"NFS 마운트 상태 확인 또는 해제 중 오류 발생 (계속 진행): {e}")
            
            # 잠시 대기
            time.sleep(1)
            
            try:
                # NFS 마운트 시도
                logging.debug("NFS 마운트 시도")
                self.manager._mount_nfs()
                
                # 마운트 상태 다시 확인
                is_mounted = self._is_nfs_mount_present(mount_path, self.manager.config.NFS_PATH)
                
                if is_mounted:
                    return jsonify({"status": "success", "message": "NFS가 성공적으로 재마운트되었습니다."})
                else:
                    return jsonify({"status": "error", "message": "NFS 마운트 시도했으나 마운트 상태를 확인할 수 없습니다."}), 500
            except Exception as e:
                logging.error(f"NFS 재마운트 실패: {e}")
                return jsonify({"status": "error", "message": f"NFS 재마운트 실패: {str(e)}"}), 500
                
        except Exception as e:
            logging.error(f"NFS 재마운트 요청 처리 중 오류: {e}")
            return jsonify({"status": "error", "message": f"NFS 재마운트 요청 실패: {str(e)}"}), 500

    def get_config(self):
        """설정 정보 제공"""
        if not self.config:
            return jsonify({"error": "설정이 로드되지 않았습니다."}), 500

        yaml_path = CONFIG_PATH

        try:
            if os.path.exists(yaml_path):
                with open(yaml_path, 'r', encoding='utf-8') as f:
                    yaml_config = yaml.safe_load(f)
            else:
                yaml_config = {}

            config_data = self._config_to_form_data(yaml_config, mask_secrets=True)

            return jsonify(config_data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    def update_config(self):
        """설정 업데이트"""
        try:
            data = request.json
            GshareConfig.update_yaml_config(data)

            return jsonify({
                "status": "success",
                "message": "설정이 업데이트되었습니다. 변경사항을 적용하려면 서비스를 재시작하세요."
            })
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    def get_transcoding_config(self):
        """트랜스코딩 설정 정보 제공"""
        try:
            yaml_path = CONFIG_PATH
            if os.path.exists(yaml_path):
                with open(yaml_path, 'r', encoding='utf-8') as f:
                    yaml_config = yaml.safe_load(f)
                transcoding = yaml_config.get('transcoding', {})
            else:
                transcoding = {}

            return jsonify({
                'enabled': transcoding.get('enabled', False),
                'rules': transcoding.get('rules', [])
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    def update_transcoding_config(self):
        """트랜스코딩 설정 업데이트"""
        try:
            data = request.json
            if data is None:
                return jsonify({"status": "error", "message": "요청 데이터가 없습니다."}), 400

            config_dict = {
                'TRANSCODING_ENABLED': data.get('enabled', False),
                'TRANSCODING_RULES': data.get('rules', [])
            }
            GshareConfig.update_yaml_config(config_dict)

            # 실행 중인 transcoder에 설정 반영
            if self.manager and hasattr(self.manager, 'transcoder'):
                self.manager.transcoder.enabled = config_dict['TRANSCODING_ENABLED']
                self.manager.transcoder.rules = config_dict['TRANSCODING_RULES']
                logging.info(f"트랜스코딩 설정 업데이트됨 (활성화: {config_dict['TRANSCODING_ENABLED']}, 규칙 {len(config_dict['TRANSCODING_RULES'])}개)")

            return jsonify({
                "status": "success",
                "message": "트랜스코딩 설정이 업데이트되었습니다."
            })
        except Exception as e:
            logging.error(f"트랜스코딩 설정 업데이트 실패: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    def scan_transcoding(self):
        """트랜스코딩 수동 스캔 시작"""
        try:
            if self.manager is None:
                return jsonify({"status": "error", "message": "서버가 아직 초기화되지 않았습니다."}), 404

            if not hasattr(self.manager, 'transcoder'):
                return jsonify({"status": "error", "message": "트랜스코더가 초기화되지 않았습니다."}), 500

            if self.manager.transcoder._processing:
                return jsonify({"status": "error", "message": "트랜스코딩이 이미 진행 중입니다."}), 409

            mount_path = self.manager.config.MOUNT_PATH
            
            # 스캔 전 config.yaml에서 최신 규칙 재로드
            try:
                config_path = CONFIG_PATH
                if os.path.exists(config_path):
                    with open(config_path, 'r', encoding='utf-8') as f:
                        yaml_config = yaml.safe_load(f)
                    transcoding = yaml_config.get('transcoding', {})
                    latest_rules = transcoding.get('rules', [])
                    self.manager.transcoder.rules = latest_rules
                    logging.info(f"스캔 전 규칙 재로드 완료: {len(latest_rules)}개")
            except Exception as e:
                logging.warning(f"스캔 전 규칙 재로드 실패: {e}")
            
            logging.info(f"스캔 요청 수신 - mount_path: {mount_path}, rules: {len(self.manager.transcoder.rules)}개")

            def progress_callback(status):
                """SocketIO로 진행 상황 전송"""
                try:
                    self.socketio.emit('transcoding_progress', status)
                except Exception as e:
                    logging.error(f"트랜스코딩 진행 상황 전송 오류: {e}")

            def run_scan():
                try:
                    # GShareManager가 이미 감시 중인 서브폴더 목록을 전달
                    subfolders = list(self.manager.folder_monitor.previous_mtimes.keys()) if self.manager.folder_monitor else None
                    self.manager.transcoder.scan_all_folders(mount_path, progress_callback, subfolders=subfolders)
                except Exception as e:
                    import traceback
                    logging.error(f"스캔 스레드 오류: {e}\n{traceback.format_exc()}")
                    try:
                        self.socketio.emit('transcoding_progress', {
                            'phase': 'error',
                            'total_files': 0, 'current_index': 0, 'current_file': '',
                            'completed': 0, 'failed': 0,
                            'message': f'스캔 스레드 오류: {str(e)}'
                        })
                    except Exception:
                        pass

            scan_thread = threading.Thread(target=run_scan, daemon=True)
            scan_thread.start()

            return jsonify({
                "status": "success",
                "message": "트랜스코딩 스캔이 시작되었습니다."
            })
        except Exception as e:
            logging.error(f"트랜스코딩 스캔 시작 실패: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500

    def cancel_transcoding_scan(self):
        """트랜스코딩 스캔 취소"""
        try:
            if self.manager and hasattr(self.manager, 'transcoder'):
                self.manager.transcoder.cancel_scan()
                return jsonify({"status": "success", "message": "스캔 취소 요청됨"})
            return jsonify({"status": "error", "message": "트랜스코더가 없습니다."}), 404
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    def get_scan_status(self):
        """현재 트랜스코딩 스캔 상태 반환 (새로고침 후 복구용)"""
        try:
            if self.manager and hasattr(self.manager, 'transcoder'):
                status = self.manager.transcoder.scan_status.copy()
                status['is_processing'] = self.manager.transcoder._processing
                return jsonify(status)
            return jsonify({'phase': 'idle', 'is_processing': False})
        except Exception as e:
            return jsonify({'phase': 'idle', 'is_processing': False, 'error': str(e)})

    def test_proxmox_api(self):
        """Proxmox API 연결 테스트"""
        try:
            proxmox_host = request.form.get('proxmox_host')
            node_name = request.form.get('node_name')
            vm_id = request.form.get('vm_id')
            token_id = request.form.get('token_id')
            secret = request.form.get('secret')
            proxmox_timeout = int(request.form.get('proxmox_timeout') or 5)

            if not all([proxmox_host, node_name, vm_id, token_id, secret]):
                return jsonify({
                    "status": "error",
                    "message": "필수 항목이 누락되었습니다."
                }), 400

            session = requests.Session()
            session.verify = False
            session.headers.update({
                "Authorization": f"PVEAPIToken={token_id}={secret}"
            })

            try:
                version_response = session.get(
                    f"{proxmox_host}/version", timeout=(proxmox_timeout, 10))
                version_response.raise_for_status()

                node_response = session.get(
                    f"{proxmox_host}/nodes/{node_name}", timeout=(proxmox_timeout, 10))
                node_response.raise_for_status()

                vm_response = session.get(
                    f"{proxmox_host}/nodes/{node_name}/qemu/{vm_id}/status/current", timeout=(proxmox_timeout, 10))
                vm_response.raise_for_status()

                vm_status = vm_response.json()["data"]["status"]

                return jsonify({
                    "status": "success",
                    "message": f"Proxmox API 연결 성공! VM 상태: {vm_status}",
                    "detail": {
                        "api_version": version_response.json()["data"]["version"],
                        "vm_status": vm_status
                    }
                })
            except requests.exceptions.SSLError:
                return jsonify({
                    "status": "error",
                    "message": "SSL 인증서 오류. HTTPS URL이 올바른지 확인하세요."
                })
            except requests.exceptions.ConnectionError:
                return jsonify({
                    "status": "error",
                    "message": "서버 연결 실패. 호스트 주소가 올바른지 확인하세요."
                })
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 401:
                    return jsonify({
                        "status": "error",
                        "message": "인증 실패. API 토큰 ID와 시크릿이 올바른지 확인하세요."
                    })
                elif e.response.status_code == 404:
                    url_path = e.response.url.split(proxmox_host)[1]
                    if '/nodes/' in url_path:
                        if f'/qemu/{vm_id}' in url_path:
                            return jsonify({
                                "status": "error",
                                "message": f"VM ID가 올바르지 않습니다. VM ID '{vm_id}'를 찾을 수 없습니다."
                            })
                        else:
                            return jsonify({
                                "status": "error",
                                "message": f"노드 이름이 올바르지 않습니다. 노드 '{node_name}'을 찾을 수 없습니다."
                            })
                return jsonify({
                    "status": "error",
                    "message": f"API 요청 실패: {str(e)}"
                })
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"Proxmox API 테스트 실패: {str(e)}"
            }), 500

    def test_nfs(self):
        """NFS 연결 테스트"""
        try:
            nfs_path = request.form.get('nfs_path')
            if not nfs_path:
                return jsonify({
                    "status": "error",
                    "message": "NFS 경로가 제공되지 않았습니다."
                }), 400

            # 먼저 이미 마운트되어 있는지 확인
            mount_check = subprocess.run(
                ['mount', '-t', 'nfs'], capture_output=True, text=True)

            # 이미 마운트된 경우 해당 마운트 지점 확인
            if nfs_path in mount_check.stdout:
                logging.debug(f"이미 마운트된 NFS 경로입니다: {nfs_path}")

                # 마운트 포인트 찾기
                mount_lines = mount_check.stdout.strip().split('\n')
                existing_mount_point = None

                for line in mount_lines:
                    if nfs_path in line:
                        parts = line.split(' on ')
                        if len(parts) > 1:
                            mount_point = parts[1].split(' ')[0]
                            existing_mount_point = mount_point
                            break

                if existing_mount_point:
                    logging.debug(f"기존 마운트 지점에서 테스트: {existing_mount_point}")
                    try:
                        # 기존 마운트 지점에서 읽기 테스트
                        ls_result = subprocess.run(
                            f"ls {existing_mount_point}", shell=True, check=True, capture_output=True, text=True)
                        return jsonify({
                            "status": "success",
                            "message": f"NFS 경로({nfs_path})가 이미 {existing_mount_point}에 마운트되어 있으며, 정상 작동합니다."
                        })
                    except Exception as e:
                        return jsonify({
                            "status": "warning",
                            "message": f"NFS 경로({nfs_path})가 이미 {existing_mount_point}에 마운트되어 있으나, 읽기 권한이 없습니다: {str(e)}"
                        })

            # 기존 마운트가 없거나 찾지 못한 경우 새로 테스트
            with tempfile.TemporaryDirectory() as temp_mount:
                try:
                    # 마운트 명령 수정 (temp_mount가 두 번 반복되는 오류 수정)
                    mount_cmd = f"mount -t nfs -o nolock,vers=3,soft,timeo=100 {nfs_path} {temp_mount}"
                    result = subprocess.run(
                        mount_cmd, shell=True, capture_output=True, text=True, timeout=60)

                    if result.returncode == 0:
                        try:
                            ls_result = subprocess.run(
                                f"ls {temp_mount}", shell=True, check=True, capture_output=True, text=True)
                            message = "NFS 연결 테스트 성공: 읽기 권한이 정상입니다."
                            status = "success"
                        except Exception as e:
                            message = f"NFS 마운트는 성공했으나 읽기 권한이 없습니다: {str(e)}"
                            status = "warning"
                    else:
                        error_msg = result.stderr.strip()
                        message = f"NFS 마운트 실패: {error_msg}"
                        status = "error"
                except subprocess.TimeoutExpired:
                    message = "NFS 마운트 시도 시간 초과"
                    status = "error"
                except Exception as e:
                    message = f"NFS 테스트 중 오류 발생: {str(e)}"
                    status = "error"
                finally:
                    try:
                        subprocess.run(
                            f"umount {temp_mount}", shell=True, check=False)
                    except:
                        pass

            return jsonify({
                "status": status,
                "message": message
            })
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"NFS 테스트 실패: {str(e)}"
            }), 500

    def test_mqtt(self):
        """MQTT 연결 테스트"""
        try:
            broker = request.form.get('mqtt_broker')
            try:
                port = int(request.form.get('mqtt_port', 1883))
            except ValueError:
                return jsonify({
                    "status": "error",
                    "message": "포트는 숫자여야 합니다."
                }), 400

            username = request.form.get('mqtt_username')
            password = request.form.get('mqtt_password')

            if not broker:
                return jsonify({
                    "status": "error",
                    "message": "브로커 주소가 입력되지 않았습니다."
                }), 400

            import paho.mqtt.client as mqtt
            import queue

            # 결과 큐
            result_queue = queue.Queue()

            def on_connect(client, userdata, flags, rc):
                if rc == 0:
                    result_queue.put({"status": "success", "message": "MQTT 브로커 연결 성공!"})
                else:
                    result_queue.put({"status": "error", "message": f"MQTT 연결 실패 (코드: {rc})"})

            client = mqtt.Client()
            if username and password:
                client.username_pw_set(username, password)

            client.on_connect = on_connect

            try:
                client.connect(broker, port, 5)
                client.loop_start()

                try:
                    result = result_queue.get(timeout=5)
                    return jsonify(result)
                except queue.Empty:
                    return jsonify({"status": "error", "message": "MQTT 연결 시간 초과"}), 500
                finally:
                    client.loop_stop()
                    client.disconnect()
            except Exception as e:
                 return jsonify({"status": "error", "message": f"MQTT 연결 오류: {str(e)}"}), 500

        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"MQTT 테스트 중 오류 발생: {str(e)}"
            }), 500

    def import_config(self):
        """업로드된 설정 파일 가져오기"""
        try:
            logging.debug("설정 파일 업로드 요청 처리 중...")

            if 'config_file' not in request.files:
                logging.warning("업로드된 파일이 없습니다.")
                return jsonify({
                    "status": "error",
                    "message": "업로드된 파일이 없습니다."
                }), 400

            file = request.files['config_file']

            if file.filename == '' or file.filename is None:
                logging.warning("선택된 파일이 없습니다.")
                return jsonify({
                    "status": "error",
                    "message": "선택된 파일이 없습니다."
                }), 400

            if not (file.filename.endswith('.yaml') or file.filename.endswith('.yml')):
                logging.warning(f"잘못된 파일 형식: {file.filename}")
                return jsonify({
                    "status": "error",
                    "message": "YAML 파일(.yaml 또는 .yml)만 업로드 가능합니다."
                }), 400

            try:
                file_content = file.read().decode('utf-8')
                yaml_config = yaml.safe_load(file_content)

                if not yaml_config:
                    logging.warning("빈 YAML 파일이 업로드되었습니다.")
                    return jsonify({
                        "status": "error",
                        "message": "업로드된 파일이 비어있거나 유효한 YAML 형식이 아닙니다."
                    }), 400

                required_sections = ['credentials', 'proxmox']
                missing_sections = [
                    section for section in required_sections if section not in yaml_config]

                if missing_sections:
                    logging.warning(f"필수 섹션 누락: {missing_sections}")
                    return jsonify({
                        "status": "warning",
                        "message": f"설정 파일에 필수 섹션이 누락되었습니다: {', '.join(missing_sections)}"
                    }), 200

                required_credentials = ['proxmox_host', 'token_id', 'secret']
                missing_credentials = []

                for cred in required_credentials:
                    if cred not in yaml_config['credentials'] or not yaml_config['credentials'][cred]:
                        missing_credentials.append(cred)

                if missing_credentials:
                    logging.warning(
                        f"필수 자격 증명이 누락되었습니다: {missing_credentials}")
                    return jsonify({
                        "status": "warning",
                        "message": f"필수 자격 증명이 누락되었습니다: {', '.join(missing_credentials)}"
                    }), 200

                form_data = {
                    'PROXMOX_HOST': yaml_config.get('credentials', {}).get('proxmox_host', ''),
                    'NODE_NAME': yaml_config.get('proxmox', {}).get('node_name', ''),
                    'VM_ID': yaml_config.get('proxmox', {}).get('vm_id', ''),
                    'TOKEN_ID': yaml_config.get('credentials', {}).get('token_id', ''),
                    'SECRET': yaml_config.get('credentials', {}).get('secret', ''),
                    'CPU_THRESHOLD': yaml_config.get('proxmox', {}).get('cpu', {}).get('threshold', 10),
                    'CHECK_INTERVAL': yaml_config.get('proxmox', {}).get('cpu', {}).get('check_interval', 60),
                    'THRESHOLD_COUNT': yaml_config.get('proxmox', {}).get('cpu', {}).get('threshold_count', 3),
                    'MOUNT_PATH': yaml_config.get('mount', {}).get('path', '/mnt/gshare'),
                    'GET_FOLDER_SIZE_TIMEOUT': yaml_config.get('mount', {}).get('folder_size_timeout', 30),
                    'NFS_PATH': yaml_config.get('nfs', {}).get('path', ''),
                    'SHUTDOWN_WEBHOOK_URL': yaml_config.get('credentials', {}).get('shutdown_webhook_url', ''),
                    'SMB_USERNAME': yaml_config.get('credentials', {}).get('smb_username', ''),
                    'SMB_PASSWORD': yaml_config.get('credentials', {}).get('smb_password', ''),
                    'SMB_SHARE_NAME': yaml_config.get('smb', {}).get('share_name', 'gshare'),
                    'SMB_COMMENT': yaml_config.get('smb', {}).get('comment', 'GShare SMB 공유'),
                    'SMB_GUEST_OK': 'yes' if yaml_config.get('smb', {}).get('guest_ok', False) else 'no',
                    'SMB_READ_ONLY': 'yes' if yaml_config.get('smb', {}).get('read_only', True) else 'no',
                    'SMB_LINKS_DIR': yaml_config.get('smb', {}).get('links_dir', '/mnt/gshare_links'),
                    'TIMEZONE': yaml_config.get('timezone', 'Asia/Seoul'),
                    'MQTT_BROKER': yaml_config.get('mqtt', {}).get('broker', ''),
                    'MQTT_PORT': yaml_config.get('mqtt', {}).get('port', 1883),
                    'MQTT_USERNAME': yaml_config.get('credentials', {}).get('mqtt_username', ''),
                    'MQTT_PASSWORD': yaml_config.get('credentials', {}).get('mqtt_password', ''),
                    'MQTT_TOPIC_PREFIX': yaml_config.get('mqtt', {}).get('topic_prefix', 'gshare'),
                    'HA_DISCOVERY_PREFIX': yaml_config.get('mqtt', {}).get('ha_discovery_prefix', 'homeassistant'),
                    'MONITOR_MODE': yaml_config.get('monitoring', {}).get('mode', 'event'),
                    'EVENT_AUTH_TOKEN': yaml_config.get('credentials', {}).get('event_auth_token', '')
                }

                temp_config_path = '/tmp/imported_config.yaml'
                try:
                    with open(temp_config_path, 'w') as f:
                        yaml.dump(yaml_config, f, allow_unicode=True,
                                  default_flow_style=False)
                    logging.debug(f"가져온 설정을 임시 파일에 저장했습니다: {temp_config_path}")
                except Exception as e:
                    logging.error(f"임시 설정 파일 저장 실패: {e}")

                logging.debug("설정 파일 가져오기 성공")
                return jsonify({
                    "status": "success",
                    "message": "설정 파일을 성공적으로 가져왔습니다. 폼에 값이 자동으로 채워졌습니다.",
                    "data": form_data
                })

            except yaml.YAMLError as e:
                logging.error(f"YAML 파싱 오류: {e}")
                return jsonify({
                    "status": "error",
                    "message": f"YAML 파싱 오류: {str(e)}"
                }), 400

        except Exception as e:
            logging.error(f"설정 파일 가져오기 중 오류 발생: {e}")
            return jsonify({
                "status": "error",
                "message": f"설정 파일 가져오기 중 오류 발생: {str(e)}"
            }), 500

    def export_config(self):
        """현재 설정 파일 내보내기"""
        try:
            config_path = CONFIG_PATH

            if not os.path.exists(config_path):
                logging.warning("내보낼 설정 파일이 없습니다.")
                return jsonify({
                    "status": "error",
                    "message": "설정 파일이 없습니다."
                }), 404

            with open(config_path, 'r', encoding='utf-8') as f:
                yaml_config = yaml.safe_load(f)

            if not yaml_config:
                logging.warning("설정 파일이 비어있습니다.")
                return jsonify({
                    "status": "error",
                    "message": "설정 파일이 비어있습니다."
                }), 400

            yaml_content = yaml.dump(
                yaml_config, allow_unicode=True, default_flow_style=False)

            current_time = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"gshare_config_{current_time}.yaml"

            response = self.app.response_class(
                response=yaml_content,
                status=200,
                mimetype='application/x-yaml'
            )
            response.headers["Content-Disposition"] = f"attachment; filename={filename}"

            logging.debug("설정 파일 내보내기 성공")
            return response

        except Exception as e:
            logging.error(f"설정 파일 내보내기 중 오류 발생: {e}")
            return jsonify({
                "status": "error",
                "message": f"설정 파일 내보내기 중 오류 발생: {str(e)}"
            }), 500

    def restart_app(self):
        """앱 재시작 엔드포인트"""
        try:
            logging.info("──────────────────────────────────────────────────")
            logging.info("앱 재시작 요청 받음")
            logging.info("──────────────────────────────────────────────────")

            restart_flag_path = RESTART_FLAG_PATH
            with open(restart_flag_path, 'w') as f:
                f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

            restart_thread = threading.Thread(target=self._delayed_restart)
            restart_thread.daemon = True
            restart_thread.start()

            logging.info("앱 재시작 요청 처리 완료")
            return jsonify({"status": "success", "message": "앱이 재시작됩니다."})
        except Exception as e:
            logging.error(f"앱 재시작 중 오류 발생: {e}")
            return jsonify({"status": "error", "message": f"재시작 중 오류 발생: {str(e)}"}), 500

    def check_restart_status(self):
        """서버 재시작 상태 확인 엔드포인트"""
        try:
            restart_flag_path = RESTART_FLAG_PATH

            if os.path.exists(restart_flag_path):
                return jsonify({"restart_complete": False})
            else:
                return jsonify({"restart_complete": True})
        except Exception as e:
            logging.error(f"재시작 상태 확인 중 오류 발생: {e}")
            return jsonify({"restart_complete": False})

    def run_landing_page(self):
        """랜딩 페이지 전용 웹서버 실행"""
        try:
            logging.info("──────────────────────────────────────────────────")
            logging.info("랜딩 페이지 모드로 웹 서버 시작")
            logging.info("──────────────────────────────────────────────────")
            self.is_setup_complete = False
            self.run_flask_app()
        except Exception as e:
            logging.error(f"랜딩 페이지 웹 서버 실행 중 오류 발생: {e}")
            logging.error(f"상세 오류: {traceback.format_exc()}")
            sys.exit(1)

    def run_flask_app(self):
        """Flask 웹 애플리케이션 실행"""
        try:
            host = '0.0.0.0'
            port = int(os.environ.get('FLASK_PORT', 5000))
            logging.info(f"웹 서버 바인딩: {host}:{port}")

            from werkzeug.serving import BaseWSGIServer  # type: ignore
            import socket

            original_server_bind = BaseWSGIServer.server_bind

            def custom_server_bind(self):
                self.socket.setsockopt(
                    socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                return original_server_bind(self)

            BaseWSGIServer.server_bind = custom_server_bind

            # app.run 대신 socketio.run 사용 - allow_unsafe_werkzeug=True 추가
            self.socketio.run(self.app, host=host, port=port, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
        except OSError as e:
            if e.errno == 98:
                logging.warning(f"포트 {port} 이미 사용 중: 다른 포트로 시도합니다.")
                os.environ['FLASK_PORT'] = str(port + 1)
                return self.run_flask_app()
            else:
                logging.error(f"Flask 웹 서버 실행 중 오류 발생: {e}")
                logging.error(f"상세 오류: {traceback.format_exc()}")
                time.sleep(10)
                return self.run_flask_app()
        except Exception as e:
            logging.error(f"Flask 웹 서버 실행 중 오류 발생: {e}")
            logging.error(f"상세 오류: {traceback.format_exc()}")
            time.sleep(10)
            logging.info("Flask 웹 서버 재시작 시도...")
            return self.run_flask_app()

    def _delayed_restart(self):
        """앱 재시작을 위한 함수"""
        time.sleep(1)
        logging.info("──────────────────────────────────────────────────")
        logging.info("앱 재시작 실행 중...")
        logging.info("──────────────────────────────────────────────────")

        try:
            restart_flag_path = RESTART_FLAG_PATH

            try:
                flask_port = int(os.environ.get('FLASK_PORT', 5000))
                current_pid = os.getpid()

                try:
                    result = subprocess.run(
                        ["ps", "-ef"],
                        capture_output=True,
                        text=True,
                        check=False
                    )

                    killed = False
                    for line in result.stdout.split('\n'):
                        if 'python' in line and 'gshare_manager.py' in line:
                            parts = line.split()
                            if len(parts) > 1:
                                pid = parts[1]
                                if pid and pid.isdigit() and int(pid) != current_pid:
                                    logging.debug(
                                        f"Python 프로세스(PID: {pid}) 종료 시도...")
                                    subprocess.run(
                                        ["kill", "-9", pid], check=False)
                                    killed = True

                    if killed:
                        time.sleep(1)
                except Exception as e:
                    logging.warning(f"Python 프로세스 종료 시도 중 오류: {e}")

                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                result = sock.connect_ex(('127.0.0.1', flask_port))
                sock.close()

                if result == 0:
                    logging.warning(
                        f"포트 {flask_port}이 여전히 사용 중입니다. 다른 포트로 변경합니다.")
                    flask_port += 1
                    os.environ['FLASK_PORT'] = str(flask_port)
                else:
                    logging.debug(f"포트 {flask_port}이 사용 가능합니다.")

            except Exception as e:
                logging.warning(f"프로세스 확인/종료 중 오류: {e}")

            main_script = os.path.join('/app', 'gshare_manager.py')

            logging.debug(f"앱 재시작 실행 명령: {sys.executable} {main_script}")

            env = os.environ.copy()

            subprocess.Popen(
                [sys.executable, "-u", main_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0
            )

            def exit_current_process():
                time.sleep(2)

                try:
                    if os.path.exists(restart_flag_path):
                        os.remove(restart_flag_path)
                        logging.debug("재시작 완료 플래그 파일 삭제됨")
                except Exception as e:
                    logging.error(f"재시작 플래그 파일 삭제 중 오류: {e}")

                logging.info(
                    "──────────────────────────────────────────────────")
                logging.info("현재 프로세스를 종료합니다...")
                logging.info(
                    "──────────────────────────────────────────────────")
                os._exit(0)

            exit_thread = threading.Thread(target=exit_current_process)
            exit_thread.daemon = True
            exit_thread.start()

        except Exception as e:
            logging.error(f"앱 재시작 중 오류 발생: {e}")
            try:
                if os.path.exists(restart_flag_path):
                    os.remove(restart_flag_path)
                    logging.debug("오류 발생으로 재시작 플래그 파일 삭제됨")
            except Exception as ex:
                logging.error(f"재시작 플래그 파일 삭제 중 오류: {ex}")

    def _start_state_update_timer(self):
        """5초마다 상태 업데이트를 수행하는 타이머 시작"""
        logging.debug("5초 주기 상태 업데이트 타이머 시작")
        self.state_update_timer = threading.Thread(
            target=self._state_update_loop, daemon=True)
        self.state_update_timer.start()
        
    def _stop_state_update_timer(self):
        """상태 업데이트 타이머 중지"""
        logging.debug("상태 업데이트 타이머 중지")
        self.state_update_timer = None  # 데몬 스레드이므로 자동으로 종료됨
        
    def _state_update_loop(self):
        """5초마다 상태 업데이트를 수행하는 루프"""
        while self.state_update_timer is not None:
            try:
                # 연결이 없거나 manager가 없는 경우 종료
                if self.active_connections == 0 or self.manager is None:
                    break
                    
                # monitored_folders를 업데이트하지 않는 가벼운 상태 업데이트 수행
                self.manager.current_state = self.manager.update_state(update_monitored_folders=False)
                self.emit_state_update()
                
                # 로그 업데이트 전송
                self.emit_log_update()
                
                # 5초 대기
                time.sleep(5)
            except Exception as e:
                logging.error(f"상태 업데이트 루프 중 오류: {e}")
                time.sleep(5)  # 오류 발생 시에도 계속 실행
