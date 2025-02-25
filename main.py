import logging
import threading
import time
import sys
import atexit

from config import Config
from proxmox_api import ProxmoxAPI
from gshare_manager import GShareManager
from web_server import run_flask_app
from utils import setup_logging

def main():
    # 로깅 설정
    setup_logging()
    
    try:
        # 설정 로드
        config = Config.load_config()
    except Exception as e:
        logging.error(f"설정을 로드할 수 없습니다: {e}")
        sys.exit(1)
    
    # Flask 스레드 먼저 시작
    flask_thread = threading.Thread(target=run_flask_app)
    flask_thread.daemon = True
    flask_thread.start()
    
    try:
        logging.info("───────────────────────────────────────────────")
        proxmox_api = ProxmoxAPI(config)
        
        # GShareManager 인스턴스 생성 및 전역 변수로 설정
        import gshare_manager
        gshare_manager.gshare_manager = GShareManager(config, proxmox_api)
        
        logging.info(f"VM is_running - {gshare_manager.gshare_manager.proxmox_api.is_vm_running()}")
        logging.info("GShare 관리 시작")
        
        # 모니터링 시작
        gshare_manager.gshare_manager.monitor()
    except KeyboardInterrupt:
        logging.info("프로그램 종료")
        logging.info("───────────────────────────────────────────────")
    except Exception as e:
        logging.error(f"예상치 못한 오류 발생: {e}")
        logging.info("───────────────────────────────────────────────")
        # 오류가 발생해도 Flask 서버는 계속 실행
        while True:
            time.sleep(60)  # 1분마다 체크

# 프로그램 종료 시 호출
def cleanup():
    try:
        import gshare_manager
        if hasattr(gshare_manager, 'gshare_manager') and gshare_manager.gshare_manager:
            gshare_manager.gshare_manager.folder_monitor.cleanup_resources()
    except Exception as e:
        logging.error(f"리소스 정리 중 오류 발생: {e}")

if __name__ == '__main__':
    atexit.register(cleanup)
    main() 