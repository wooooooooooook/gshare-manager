from dataclasses import dataclass
from dotenv import load_dotenv
import os
import yaml
from typing import Dict, Any

load_dotenv()

@dataclass
class Config:
    # Proxmox android 사용량 감시
    ## Proxmox API 호스트
    PROXMOX_HOST: str
    ## Proxmox 노드 이름
    NODE_NAME: str
    ## android VM ID
    VM_ID: str
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
    TIMEZONE: str

    @classmethod
    def load_config(cls) -> 'Config':
        # .env에서 민감한 정보 로드
        required_env_vars = {
            'PROXMOX_HOST': os.getenv('PROXMOX_HOST'),
            'TOKEN_ID': os.getenv('TOKEN_ID'),
            'SECRET': os.getenv('SECRET'),
            'SHUTDOWN_WEBHOOK_URL': os.getenv('SHUTDOWN_WEBHOOK_URL'),
            'SMB_USERNAME': os.getenv('SMB_USERNAME'),
            'SMB_PASSWORD': os.getenv('SMB_PASSWORD')
        }
        
        # 필수 환경 변수 확인
        missing_vars = [var for var, value in required_env_vars.items() if not value]
        if missing_vars:
            raise ValueError(f"다음 환경변수가 설정되지 않았습니다: {', '.join(missing_vars)}")

        # YAML 설정 파일 로드
        try:
            with open('config.yaml', 'r', encoding='utf-8') as f:
                yaml_config = yaml.safe_load(f)
        except Exception as e:
            raise ValueError(f"YAML 설정 파일을 로드할 수 없습니다: {e}")

        # YAML 설정에서 값 추출
        config_dict = {
            'PROXMOX_HOST': required_env_vars['PROXMOX_HOST'],
            'NODE_NAME': yaml_config['proxmox']['node_name'],
            'VM_ID': yaml_config['proxmox']['vm_id'],
            'TOKEN_ID': required_env_vars['TOKEN_ID'],
            'SECRET': required_env_vars['SECRET'],
            'CPU_THRESHOLD': yaml_config['proxmox']['cpu']['threshold'],
            'CHECK_INTERVAL': yaml_config['proxmox']['cpu']['check_interval'],
            'THRESHOLD_COUNT': yaml_config['proxmox']['cpu']['threshold_count'],
            'MOUNT_PATH': yaml_config['mount']['path'],
            'GET_FOLDER_SIZE_TIMEOUT': yaml_config['mount']['folder_size_timeout'],
            'SHUTDOWN_WEBHOOK_URL': required_env_vars['SHUTDOWN_WEBHOOK_URL'],
            'SMB_SHARE_NAME': yaml_config['smb']['share_name'],
            'SMB_USERNAME': required_env_vars['SMB_USERNAME'],
            'SMB_PASSWORD': required_env_vars['SMB_PASSWORD'],
            'SMB_COMMENT': yaml_config['smb']['comment'],
            'SMB_GUEST_OK': yaml_config['smb']['guest_ok'],
            'SMB_READ_ONLY': yaml_config['smb']['read_only'],
            'SMB_LINKS_DIR': yaml_config['smb']['links_dir'],
            'TIMEZONE': yaml_config['timezone']
        }

        return cls(**config_dict)

    @staticmethod
    def update_yaml_config(config_dict: Dict[str, Any]) -> None:
        """YAML 설정 파일 업데이트"""
        # 현재 설정 로드
        with open('config.yaml', 'r', encoding='utf-8') as f:
            yaml_config = yaml.safe_load(f)

        # 설정 업데이트
        if 'NODE_NAME' in config_dict:
            yaml_config['proxmox']['node_name'] = config_dict['NODE_NAME']
        if 'VM_ID' in config_dict:
            yaml_config['proxmox']['vm_id'] = config_dict['VM_ID']
        if 'CPU_THRESHOLD' in config_dict:
            yaml_config['proxmox']['cpu']['threshold'] = float(config_dict['CPU_THRESHOLD'])
        if 'CHECK_INTERVAL' in config_dict:
            yaml_config['proxmox']['cpu']['check_interval'] = int(config_dict['CHECK_INTERVAL'])
        if 'THRESHOLD_COUNT' in config_dict:
            yaml_config['proxmox']['cpu']['threshold_count'] = int(config_dict['THRESHOLD_COUNT'])
        if 'MOUNT_PATH' in config_dict:
            yaml_config['mount']['path'] = config_dict['MOUNT_PATH']
        if 'GET_FOLDER_SIZE_TIMEOUT' in config_dict:
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
        if 'TIMEZONE' in config_dict:
            yaml_config['timezone'] = config_dict['TIMEZONE']

        # 설정 저장
        with open('config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(yaml_config, f, allow_unicode=True, default_flow_style=False)

    def __post_init__(self):
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