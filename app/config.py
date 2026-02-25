from dataclasses import dataclass
import os
import yaml
from typing import Dict, Any, Optional, List

# 경로 관련 상수
CONFIG_DIR = '/config'
LOG_DIR = '/logs'
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.yaml')
TEMPLATE_PATH = os.path.join(CONFIG_DIR, 'config.yaml.template')
INIT_FLAG_PATH = os.path.join(CONFIG_DIR, '.init_complete')
RESTART_FLAG_PATH = os.path.join(CONFIG_DIR, '.restart_in_progress')
LAST_SHUTDOWN_PATH = os.path.join(CONFIG_DIR, '.last_shutdown')
FOLDER_SCAN_CACHE_PATH = os.path.join(CONFIG_DIR, '.folder_scan_cache.json')
LOG_FILE_PATH = os.path.join(LOG_DIR, 'gshare_manager.log')

@dataclass
class GshareConfig:
    # Proxmox android 사용량 감시
    ## Proxmox API 호스트
    PROXMOX_HOST: str
    ## Proxmox 노드 이름
    NODE_NAME: str
    ## android VM ID
    VM_ID: str
    ## API 연결 타임아웃(초)
    PROXMOX_TIMEOUT: int
    ## API 토큰 ID
    TOKEN_ID: str
    ## API 토큰 시크릿
    SECRET: str
    ## CPU 사용량 임계치(%)
    CPU_THRESHOLD: float
    ## 체크 간격(초)
    CHECK_INTERVAL: int
    ## 체크 횟수
    THRESHOLD_COUNT: int
    
    # 폴더용량 감시
    ## 감시폴더 마운트 경로
    MOUNT_PATH: str
    ## 폴더 용량 확인 시간 초과(초)
    GET_FOLDER_SIZE_TIMEOUT: int
    ## macrodroid 종료 웹훅 URL
    SHUTDOWN_WEBHOOK_URL: str
    
    # SMB 설정
    ## SMB 공유 이름
    SMB_SHARE_NAME: str
    ## SMB 사용자 이름
    SMB_USERNAME: str
    ## SMB 비밀번호
    SMB_PASSWORD: str
    ## SMB 설명
    SMB_COMMENT: str
    ## SMB 게스트 허용 여부
    SMB_GUEST_OK: bool
    ## SMB 읽기 전용 여부
    SMB_READ_ONLY: bool
    ## SMB 링크 디렉토리
    SMB_LINKS_DIR: str
    
    # 로그 시간대
    TIMEZONE: str = 'Asia/Seoul'
    
    ## SMB 포트
    SMB_PORT: int = 445
    
    # 로그 레벨 설정
    LOG_LEVEL: str = 'INFO'

    # NFS 설정
    ## NFS 공유 경로
    NFS_PATH: Optional[str] = None

    # MQTT 설정
    ## MQTT 브로커 호스트
    MQTT_BROKER: str = ''
    ## MQTT 포트
    MQTT_PORT: int = 1883
    ## MQTT 사용자 이름
    MQTT_USERNAME: str = ''
    ## MQTT 비밀번호
    MQTT_PASSWORD: str = ''
    ## MQTT 토픽 접두사
    MQTT_TOPIC_PREFIX: str = 'gshare'
    ## Home Assistant Discovery 접두사
    HA_DISCOVERY_PREFIX: str = 'homeassistant'

    # 트랜스코딩 설정
    ## 트랜스코딩 활성화 여부
    TRANSCODING_ENABLED: bool = False
    ## 트랜스코딩 규칙 목록
    TRANSCODING_RULES: List[Dict[str, Any]] = None
    ## 트랜스코딩 완료 파일명
    TRANSCODING_DONE_FILENAME: str = '.transcoding_done'

    # 이벤트 수신 기반 감시 설정
    MONITOR_MODE: str = 'event'
    EVENT_AUTH_TOKEN: str = ''

    def __post_init__(self):
        if self.TRANSCODING_RULES is None:
            self.TRANSCODING_RULES = []

    @classmethod
    def load_config(cls) -> 'GshareConfig':
        """설정 파일에서 설정 로드"""
        # Docker 환경을 가정하고 설정 파일 경로 고정
        config_path = CONFIG_PATH
        
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    yaml_config = yaml.safe_load(f)
                print(f"설정 파일 로드 완료: {config_path}")
            else:
                raise ValueError("설정 파일을 찾을 수 없습니다.")
        except Exception as e:
            print(f"설정 파일 로드 실패: {e}")
            raise ValueError("YAML 설정 파일을 로드할 수 없습니다.")

        # 필수 필드 확인
        required_fields = [
            'proxmox', 'mount', 'smb', 'timezone',
            'credentials', 'mqtt'
        ]
        
        for field in required_fields:
            if field not in yaml_config:
                yaml_config[field] = {}
        
        if 'cpu' not in yaml_config['proxmox']:
            yaml_config['proxmox']['cpu'] = {}
            
        if 'credentials' not in yaml_config:
            yaml_config['credentials'] = {}

        # 로그 레벨: 환경 변수에서 우선적으로 가져오고, 없으면 YAML에서 가져옴
        env_log_level = os.environ.get('LOG_LEVEL')
        yaml_log_level = yaml_config.get('log_level') or 'INFO'
        
        # 환경 변수가 설정되어 있고, YAML과 다른 경우 YAML 업데이트
        if env_log_level and env_log_level != yaml_log_level:
            yaml_config['log_level'] = env_log_level
            # YAML 파일 업데이트
            try:
                with open(config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(yaml_config, f, allow_unicode=True, default_flow_style=False)
                print(f"로그 레벨 업데이트됨: {env_log_level} (환경 변수 값으로)")
            except Exception as e:
                print(f"YAML 업데이트 실패: {e}")
        
        # 환경 변수에 설정이 없으면 YAML 값을 환경 변수에 설정
        elif not env_log_level:
            os.environ['LOG_LEVEL'] = yaml_log_level
        
        # 최종적으로 사용할 로그 레벨 (환경 변수 우선)
        log_level = env_log_level if env_log_level else yaml_log_level

        # YAML 설정에서 값 추출
        config_dict = {
            'PROXMOX_HOST': yaml_config['credentials'].get('proxmox_host', ''),
            'NODE_NAME': yaml_config['proxmox'].get('node_name', ''),
            'VM_ID': yaml_config['proxmox'].get('vm_id', ''),
            'PROXMOX_TIMEOUT': yaml_config['proxmox'].get('timeout') or 5,
            'TOKEN_ID': yaml_config['credentials'].get('token_id', ''),
            'SECRET': yaml_config['credentials'].get('secret', ''),
            'CPU_THRESHOLD': yaml_config['proxmox']['cpu'].get('threshold') or 10.0,
            'CHECK_INTERVAL': yaml_config['proxmox']['cpu'].get('check_interval') or 60,
            'THRESHOLD_COUNT': yaml_config['proxmox']['cpu'].get('threshold_count') or 3,
            'MOUNT_PATH': yaml_config['mount'].get('path', '/mnt/gshare'),
            'GET_FOLDER_SIZE_TIMEOUT': yaml_config['mount'].get('folder_size_timeout') or 30,
            'SHUTDOWN_WEBHOOK_URL': yaml_config['credentials'].get('shutdown_webhook_url', ''),
            'SMB_SHARE_NAME': yaml_config['smb'].get('share_name', 'gshare'),
            'SMB_USERNAME': yaml_config['credentials'].get('smb_username', ''),
            'SMB_PASSWORD': yaml_config['credentials'].get('smb_password', ''),
            'SMB_COMMENT': yaml_config['smb'].get('comment', 'GShare SMB 공유'),
            'SMB_GUEST_OK': yaml_config['smb'].get('guest_ok', False),
            'SMB_READ_ONLY': yaml_config['smb'].get('read_only', True),
            'SMB_LINKS_DIR': yaml_config['smb'].get('links_dir', '/mnt/gshare_links'),
            'SMB_PORT': yaml_config['smb'].get('port') or 445,
            'TIMEZONE': yaml_config.get('timezone') or 'Asia/Seoul',
            'LOG_LEVEL': log_level,
            'MQTT_BROKER': yaml_config['mqtt'].get('broker', ''),
            'MQTT_PORT': yaml_config['mqtt'].get('port') or 1883,
            'MQTT_USERNAME': yaml_config['credentials'].get('mqtt_username', ''),
            'MQTT_PASSWORD': yaml_config['credentials'].get('mqtt_password', ''),
            'MQTT_TOPIC_PREFIX': yaml_config['mqtt'].get('topic_prefix', 'gshare'),
            'HA_DISCOVERY_PREFIX': yaml_config['mqtt'].get('ha_discovery_prefix', 'homeassistant'),
            'TRANSCODING_ENABLED': yaml_config.get('transcoding', {}).get('enabled', False),
            'TRANSCODING_RULES': yaml_config.get('transcoding', {}).get('rules', []),
            'TRANSCODING_DONE_FILENAME': yaml_config.get('transcoding', {}).get('done_filename', '.transcoding_done'),
            'MONITOR_MODE': yaml_config.get('monitoring', {}).get('mode', 'event'),
            'EVENT_AUTH_TOKEN': yaml_config.get('credentials', {}).get('event_auth_token', '')
        }

        # NFS 설정 추가
        if 'nfs' in yaml_config and 'path' in yaml_config['nfs']:
            config_dict['NFS_PATH'] = yaml_config['nfs']['path']

        return cls(**config_dict)

    @staticmethod
    def update_yaml_config(config_dict: Dict[str, Any]) -> None:
        """YAML 설정 파일 업데이트"""
        # Docker 환경을 가정하고 설정 파일 경로 고정
        yaml_path = CONFIG_PATH
        
        try:
            if os.path.exists(yaml_path):
                with open(yaml_path, 'r', encoding='utf-8') as f:
                    yaml_config = yaml.safe_load(f)
                if yaml_config is None:
                    yaml_config = {}
            else:
                # 설정 파일이 없으면 기본 구조 생성
                yaml_config = {
                    'proxmox': {'cpu': {}},
                    'mount': {},
                    'smb': {},
                    'credentials': {},
                    'nfs': {},
                    'mqtt': {},
                    'monitoring': {},
                    'timezone': 'Asia/Seoul',
                    'log_level': 'INFO'
                }
        except Exception:
            # 파일을 읽을 수 없는 경우 기본 구조 생성
            yaml_config = {
                'proxmox': {'cpu': {}},
                'mount': {},
                'smb': {},
                'credentials': {},
                'nfs': {},
                'mqtt': {},
                'monitoring': {},
                'timezone': 'Asia/Seoul',
                'log_level': 'INFO'
            }

        # 필수 섹션이 없는 경우 초기화
        required_sections = ['proxmox', 'mount', 'smb', 'credentials', 'nfs', 'mqtt', 'monitoring']
        for section in required_sections:
            if section not in yaml_config or yaml_config[section] is None:
                yaml_config[section] = {}

        if 'proxmox' in yaml_config and yaml_config['proxmox'] is not None:
            if 'cpu' not in yaml_config['proxmox'] or yaml_config['proxmox']['cpu'] is None:
                yaml_config['proxmox']['cpu'] = {}

        # 일반 설정 업데이트
        if 'NODE_NAME' in config_dict:
            yaml_config['proxmox']['node_name'] = config_dict['NODE_NAME']
        if "VM_ID" in config_dict:
            yaml_config["proxmox"]["vm_id"] = config_dict["VM_ID"]
        if "PROXMOX_TIMEOUT" in config_dict and str(config_dict["PROXMOX_TIMEOUT"]).strip():
            yaml_config["proxmox"]["timeout"] = int(config_dict["PROXMOX_TIMEOUT"])
        if 'CPU_THRESHOLD' in config_dict and str(config_dict['CPU_THRESHOLD']).strip():
            yaml_config['proxmox']['cpu']['threshold'] = float(config_dict['CPU_THRESHOLD'])
        if 'CHECK_INTERVAL' in config_dict and str(config_dict['CHECK_INTERVAL']).strip():
            yaml_config['proxmox']['cpu']['check_interval'] = int(config_dict['CHECK_INTERVAL'])
        if 'THRESHOLD_COUNT' in config_dict and str(config_dict['THRESHOLD_COUNT']).strip():
            yaml_config['proxmox']['cpu']['threshold_count'] = int(config_dict['THRESHOLD_COUNT'])
        if 'MOUNT_PATH' in config_dict:
            yaml_config['mount']['path'] = config_dict['MOUNT_PATH']
        if 'GET_FOLDER_SIZE_TIMEOUT' in config_dict and str(config_dict['GET_FOLDER_SIZE_TIMEOUT']).strip():
            yaml_config['mount']['folder_size_timeout'] = int(config_dict['GET_FOLDER_SIZE_TIMEOUT'])
        if 'SMB_SHARE_NAME' in config_dict:
            yaml_config['smb']['share_name'] = config_dict['SMB_SHARE_NAME']
        if 'SMB_COMMENT' in config_dict:
            yaml_config['smb']['comment'] = config_dict['SMB_COMMENT']
        if 'SMB_GUEST_OK' in config_dict:
            yaml_config['smb']['guest_ok'] = config_dict['SMB_GUEST_OK'] == 'yes'
        if 'SMB_READ_ONLY' in config_dict:
            yaml_config['smb']['read_only'] = config_dict['SMB_READ_ONLY'] == 'yes'
        if 'SMB_LINKS_DIR' in config_dict:
            yaml_config['smb']['links_dir'] = config_dict['SMB_LINKS_DIR']
        if 'SMB_PORT' in config_dict and str(config_dict['SMB_PORT']).strip():
            yaml_config['smb']['port'] = int(config_dict['SMB_PORT'])
        if 'TIMEZONE' in config_dict:
            yaml_config['timezone'] = config_dict['TIMEZONE']
        # 로그 레벨 업데이트
        if 'LOG_LEVEL' in config_dict:
            yaml_config['log_level'] = config_dict['LOG_LEVEL']
        
        # MQTT 설정 업데이트
        if 'MQTT_BROKER' in config_dict:
            yaml_config['mqtt']['broker'] = config_dict['MQTT_BROKER']
        if 'MQTT_PORT' in config_dict and str(config_dict['MQTT_PORT']).strip():
            yaml_config['mqtt']['port'] = int(config_dict['MQTT_PORT'])
        if 'MQTT_TOPIC_PREFIX' in config_dict:
            yaml_config['mqtt']['topic_prefix'] = config_dict['MQTT_TOPIC_PREFIX']
        if 'HA_DISCOVERY_PREFIX' in config_dict:
            yaml_config['mqtt']['ha_discovery_prefix'] = config_dict['HA_DISCOVERY_PREFIX']

        # 민감한 정보(자격 증명) 업데이트
        if 'PROXMOX_HOST' in config_dict:
            yaml_config['credentials']['proxmox_host'] = config_dict['PROXMOX_HOST']
        if 'TOKEN_ID' in config_dict:
            yaml_config['credentials']['token_id'] = config_dict['TOKEN_ID']
        if 'SECRET' in config_dict:
            yaml_config['credentials']['secret'] = config_dict['SECRET']
        if 'SHUTDOWN_WEBHOOK_URL' in config_dict:
            yaml_config['credentials']['shutdown_webhook_url'] = config_dict['SHUTDOWN_WEBHOOK_URL']
        if 'SMB_USERNAME' in config_dict:
            yaml_config['credentials']['smb_username'] = config_dict['SMB_USERNAME']
        if 'SMB_PASSWORD' in config_dict:
            yaml_config['credentials']['smb_password'] = config_dict['SMB_PASSWORD']
        if 'MQTT_USERNAME' in config_dict:
            yaml_config['credentials']['mqtt_username'] = config_dict['MQTT_USERNAME']
        if 'MQTT_PASSWORD' in config_dict:
            yaml_config['credentials']['mqtt_password'] = config_dict['MQTT_PASSWORD']
        if 'EVENT_AUTH_TOKEN' in config_dict:
            yaml_config['credentials']['event_auth_token'] = config_dict['EVENT_AUTH_TOKEN']

        if 'MONITOR_MODE' in config_dict:
            yaml_config['monitoring']['mode'] = config_dict['MONITOR_MODE']
        
        # NFS 설정 저장
        if 'NFS_PATH' in config_dict:
            yaml_config['nfs']['path'] = config_dict['NFS_PATH']

        # 트랜스코딩 설정 저장
        if 'transcoding' not in yaml_config or yaml_config['transcoding'] is None:
            yaml_config['transcoding'] = {'enabled': False, 'rules': []}
        if 'TRANSCODING_ENABLED' in config_dict:
            yaml_config['transcoding']['enabled'] = config_dict['TRANSCODING_ENABLED']
        if 'TRANSCODING_RULES' in config_dict:
            yaml_config['transcoding']['rules'] = config_dict['TRANSCODING_RULES']

        # 설정 저장
        os.makedirs(os.path.dirname(yaml_path), exist_ok=True)
        with open(yaml_path, 'w', encoding='utf-8') as f:
            yaml.dump(yaml_config, f, allow_unicode=True, default_flow_style=False)

    @staticmethod
    def load_template_config() -> Dict[str, Any]:
        """템플릿 설정 파일 로드"""
        template_path = TEMPLATE_PATH
        
        try:
            if os.path.exists(template_path):
                with open(template_path, 'r', encoding='utf-8') as f:
                    yaml_config = yaml.safe_load(f)
                
                print(f"템플릿 설정 파일 로드 완료: {template_path}")
                
                # 기본 구조 확인 및 설정
                if yaml_config is None:
                    yaml_config = {}
                
                # credentials 섹션이 없으면 빈 딕셔너리 추가
                if 'credentials' not in yaml_config:
                    yaml_config['credentials'] = {}
                
                # 필요한 항목이 없으면 빈 값 추가
                for section in ['proxmox', 'mount', 'smb', 'nfs', 'mqtt']:
                    if section not in yaml_config:
                        yaml_config[section] = {}
                
                if 'cpu' not in yaml_config.get('proxmox', {}):
                    yaml_config['proxmox']['cpu'] = {}
                
                return yaml_config
        except Exception as e:
            print(f"템플릿 설정 파일 로드 실패: {e}")
        
        # 템플릿 파일이 없으면 기본 설정 반환
        return {
            'proxmox': {'node_name': '', 'vm_id': '', 'timeout': 5, 'cpu': {'threshold': 10.0, 'check_interval': 60, 'threshold_count': 3}},
            'mount': {'path': '/mnt/gshare', 'folder_size_timeout': 30},
            'smb': {'share_name': 'gshare', 'comment': 'GShare SMB 공유', 'guest_ok': False, 'read_only': True, 'links_dir': '/mnt/gshare_links', 'port': 445},
            'nfs': {'path': ''},
            'mqtt': {'broker': '', 'port': 1883, 'topic_prefix': 'gshare', 'ha_discovery_prefix': 'homeassistant'},
            'monitoring': {'mode': 'event'},
            'credentials': {'proxmox_host': '', 'token_id': '', 'secret': '', 'shutdown_webhook_url': '', 'smb_username': '', 'smb_password': '', 'mqtt_username': '', 'mqtt_password': '', 'event_auth_token': ''},
            'timezone': 'Asia/Seoul',
            'transcoding': {'enabled': False, 'rules': []}
        }

    def __post_init_validation__(self):
        # 초기화 시 설정 유효성 검사
        if not all([
            self.PROXMOX_HOST,
            self.TOKEN_ID,
            self.SECRET,
            self.SHUTDOWN_WEBHOOK_URL,
            self.SMB_USERNAME,
            self.SMB_PASSWORD
        ]):
            raise ValueError("필수 설정값이 누락되었습니다.")
