import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from datetime import datetime
import pytz
from dotenv import load_dotenv, set_key

def setup_logging(timezone: str = 'Asia/Seoul'):
    """로깅 설정을 초기화합니다."""
    load_dotenv()
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # 기본 시간대를 설정된 timezone으로 설정
    formatter.converter = lambda *args: datetime.now(tz=pytz.timezone(timezone)).timetuple()

    # 로그 로테이션 설정
    file_handler = RotatingFileHandler(
        'gshare_manager.log',
        maxBytes=5*1024*1024,  # 5MB
        backupCount=1,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level))

    # 기존 핸들러 제거
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

def update_log_level():
    """로그 레벨을 동적으로 업데이트합니다."""
    load_dotenv()
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    logging.getLogger().setLevel(getattr(logging, log_level))

def format_uptime(seconds: float) -> str:
    """초 단위의 시간을 읽기 쉬운 형식으로 변환합니다."""
    if seconds is None:
        return "알 수 없음"
        
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}시간 {minutes}분 {secs}초"
    elif minutes > 0:
        return f"{minutes}분 {secs}초"
    else:
        return f"{secs}초" 