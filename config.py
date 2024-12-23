from dataclasses import dataclass
from dotenv import load_dotenv
import os

load_dotenv()

@dataclass
class Config:
    # Proxmox android 사용량 감시
    ## Proxmox API 호스트
    PROXMOX_HOST: str = os.getenv('PROXMOX_HOST')
    ## Proxmox 노드 이름
    NODE_NAME: str = "woooook"
    ## android VM ID
    VM_ID: str = "101"
    ## API 토큰 ID
    TOKEN_ID: str = os.getenv('TOKEN_ID')
    ## API 토큰 시크릿
    SECRET: str = os.getenv('SECRET')
    ## CPU 사용량 임계치(%)
    CPU_THRESHOLD: float = 1.0
    ## 체크 간격(초)
    CHECK_INTERVAL: int = 120
    ## 체크 횟수
    THRESHOLD_COUNT: int = 2
    
    # 폴더용량 감시
    ## 감시폴더 마운트 경로
    MOUNT_PATH: str = "/mnt/gshare"
    ## 폴더 용량 확인 시간 초과(초)
    GET_FOLDER_SIZE_TIMEOUT: int = 30
    ## macrodroid 종료 웹훅 URL
    SHUTDOWN_WEBHOOK_URL: str = os.getenv('SHUTDOWN_WEBHOOK_URL')
    
    # 로그 시간대
    TIMEZONE: str = "Asia/Seoul"

    def __post_init__(self):
        required_env_vars = {
            'PROXMOX_HOST': self.PROXMOX_HOST,
            'TOKEN_ID': self.TOKEN_ID,
            'SECRET': self.SECRET,
            'SHUTDOWN_WEBHOOK_URL': self.SHUTDOWN_WEBHOOK_URL
        }
        
        missing_vars = [var for var, value in required_env_vars.items() if not value]
        if missing_vars:
            raise ValueError(f"다음 환경변수가 설정되지 않았습니다: {', '.join(missing_vars)}")