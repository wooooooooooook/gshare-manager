import logging
from flask import Flask, jsonify, render_template, request, redirect, url_for
import threading
import subprocess
import os
import sys
import argparse
from dataclasses import asdict
import requests
from datetime import datetime
import pytz
from dotenv import load_dotenv, set_key
import yaml
import json
import socket
import tempfile
from config import Config
import time
import traceback


# 명령행 인수 파싱
parser = argparse.ArgumentParser(description='GShare 웹서버')
parser.add_argument('--landing-only', action='store_true', help='랜딩 페이지 전용 모드로 실행')
args = parser.parse_args()

# Flask 로그 비활성화
cli = sys.modules['flask.cli']
cli.show_server_banner = lambda *x: None
app = Flask(__name__)
app.logger.disabled = True
log = logging.getLogger('werkzeug')
log.disabled = True

# 전역 변수로 상태와 관리자 객체 선언
current_state = None
gshare_manager = None
config = None
is_setup_complete = False

log_file = os.path.join('/logs', 'gshare_manager.log')


# 환경 변수에서 랜딩 전용 모드 설정을 확인하고, 명령행 인수와 결합
landing_only_env = os.environ.get('LANDING_ONLY', '').lower() in ('true', '1', 't')
is_landing_only_mode = args.landing_only or landing_only_env

# 명시적으로 일반 모드로 설정된 경우 체크
is_normal_mode = os.environ.get('LANDING_ONLY', '').lower() in ('false', '0', 'f')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logging.info(f"웹 서버 초기화 - 랜딩 전용 모드: {is_landing_only_mode}, 명시적 일반 모드: {is_normal_mode}")

def check_setup_complete():
    """설정 완료 여부 확인"""
    try:
        # 모든 실행이 Docker 환경에서 이루어진다고 가정
        config_path = '/config/config.yaml'
        init_flag_path = '/config/.init_complete'
        
        # 초기화 완료 플래그 확인
        init_flag_exists = os.path.exists(init_flag_path)
        logging.info(f"초기화 완료 플래그 확인: {init_flag_exists} (경로: {init_flag_path})")
        
        # 설정 파일 존재 확인
        config_exists = os.path.exists(config_path)
        logging.info(f"설정 파일 확인: {config_exists} (경로: {config_path})")
        
        # 두 파일 중 하나라도 없으면 설정이 완료되지 않은 것으로 판단
        if not config_exists or not init_flag_exists:
            logging.info("설정 파일 또는 초기화 플래그가 없습니다. 설정이 완료되지 않았습니다.")
            return False
            
        # 파일 수정 시간 비교
        try:
            # config.yaml 파일의 수정 시간
            config_mtime = os.path.getmtime(config_path)
            config_mtime_str = datetime.fromtimestamp(config_mtime).strftime('%Y-%m-%d %H:%M:%S')
            
            # init_flag 파일의 내용에서 시간 읽기
            with open(init_flag_path, 'r') as f:
                init_flag_time_str = f.read().strip()
                
            # init_flag 파일이 비어있거나 형식이 잘못된 경우
            if not init_flag_time_str:
                logging.warning("초기화 플래그 파일이 비어있습니다.")
                return False
                
            try:
                # 문자열을 datetime 객체로 변환
                init_flag_time = datetime.strptime(init_flag_time_str, '%Y-%m-%d %H:%M:%S')
                config_time = datetime.fromtimestamp(config_mtime)
                
                # 초기화 플래그 시간이 config 파일 수정 시간보다 이후인지 확인
                is_valid = init_flag_time >= config_time
                
                logging.info(f"설정 파일 수정 시간: {config_mtime_str}")
                logging.info(f"초기화 플래그 시간: {init_flag_time_str}")
                logging.info(f"초기화 플래그 시간이 설정 파일 수정 시간보다 이후임: {is_valid}")
                
                if not is_valid:
                    logging.warning("설정 파일이 초기화 완료 이후에 수정되었습니다. 재설정이 필요합니다.")
                    return False
                    
            except ValueError as e:
                logging.error(f"초기화 플래그 시간 형식 오류: {e}")
                # 시간 형식 오류의 경우, 플래그 파일을 업데이트
                with open(init_flag_path, 'w') as f:
                    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    f.write(current_time)
                logging.info(f"초기화 플래그 시간을 현재 시간({current_time})으로 업데이트했습니다.")
                return True
                
        except Exception as e:
            logging.error(f"파일 시간 비교 중 오류 발생: {e}")
            return False
            
        # 모든 조건을 통과하면 설정 완료로 판단
        logging.info("모든 설정 검증을 통과했습니다. 설정이 완료되었습니다.")
        return True
        
    except Exception as e:
        logging.error(f"설정 완료 여부 확인 중 오류 발생: {e}")
        return False

def init_server(state, manager, app_config):
    """웹서버 초기화"""
    try:
        logging.info("웹 서버 상태 초기화 시작")
        global current_state, gshare_manager, config, is_setup_complete
        current_state = state
        gshare_manager = manager
        config = app_config
        
        # 설정 완료 여부 확인
        is_setup_complete = check_setup_complete()
        
        if is_setup_complete:
            logging.info("설정 완료 상태가 감지되었습니다. 일반 모드로 실행합니다.")
        else:
            logging.warning("설정이 완료되지 않았습니다. 설정이 필요합니다.")
            
        logging.info("웹 서버 상태 초기화 완료")
    except Exception as e:
        logging.error(f"웹 서버 초기화 중 오류 발생: {e}")
        logging.error(f"상세 오류: {traceback.format_exc()}")
        # 오류가 발생해도 진행 시도

def get_default_state():
    """State가 초기화되지 않았을 때 사용할 기본값을 반환"""
    from gshare_manager import State
    
    # 기본 시간대를 'Asia/Seoul'로 설정
    current_time = datetime.now(tz=pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
    return State(
        last_check_time=current_time,
        vm_running=False,
        cpu_usage=0.0,
        last_action="초기화되지 않음",
        low_cpu_count=0,
        threshold_count=0,
        uptime="알 수 없음",
        last_shutdown_time="-",
        monitored_folders={},
        smb_running=False,
        check_interval=60  # 기본값 60초
    )

def get_container_ip():
    """도커 컨테이너의 IP 주소를 가져옵니다."""
    try:
        # 호스트 이름으로 IP 주소 가져오기
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        return ip_address
    except Exception as e:
        logging.error(f"IP 주소 가져오기 실패: {e}")
        return "알 수 없음"

@app.route('/')
def main_page():
    """메인 페이지 표시"""
    global is_setup_complete, is_landing_only_mode
    
    # 명시적으로 일반 모드로 설정된 경우
    if is_normal_mode:
        # 설정이 완료되었는지 다시 확인
        is_setup_complete = check_setup_complete()
        
        if is_setup_complete:
            logging.info("명시적 일반 모드 + 설정 완료: 대시보드로 이동")
            if current_state:
                return render_template('index.html', state=current_state, config=config)
            else:
                return render_template('index.html', state=get_default_state(), config=None)
    
    # 설정 완료 여부 확인
    is_setup_complete = check_setup_complete()
    
    # 설정이 완료되지 않았거나 명시적으로 랜딩 모드인 경우 설정 페이지로 리다이렉션
    if not is_setup_complete or is_landing_only_mode:
        logging.info(f"'/' 경로 접근: 설정 페이지로 리디렉션 (is_setup_complete: {is_setup_complete}, is_landing_only_mode: {is_landing_only_mode})")
        return redirect(url_for('setup'))
    
    # 기본 대시보드 표시
    logging.info("'/' 경로 접근: 설정 완료됨. 대시보드 표시.")
    if current_state:
        return render_template('index.html', state=current_state, config=config)
    else:
        return render_template('index.html', state=get_default_state(), config=None)

@app.route('/setup')
def setup():
    """초기 설정 페이지"""
    global is_landing_only_mode, is_setup_complete
    
    # 명시적으로 일반 모드로 설정된 경우
    if is_normal_mode:
        # 설정이 완료되었는지 다시 확인
        is_setup_complete = check_setup_complete()
        
        if is_setup_complete:
            logging.info("명시적 일반 모드 + 설정 완료: 메인 페이지로 리디렉션")
            return redirect(url_for('show_log'))
    
    # 설정 완료 여부 다시 확인
    is_setup_complete = check_setup_complete()
    
    # 설정이 완료되었고 랜딩 모드가 아니면 메인 페이지로 리디렉션
    if is_setup_complete and not is_landing_only_mode:
        logging.info("'/setup' 경로 접근: 설정이 이미 완료됨. 메인 페이지로 리디렉션.")
        return redirect(url_for('show_log'))
    
    # 도커 컨테이너의 IP 주소 가져오기
    container_ip = get_container_ip()
    
    # 랜딩 페이지 전용 모드 설정
    logging.info(f"현재 랜딩 페이지 모드 상태: {is_landing_only_mode}")
    
    # 이미 설정 파일이 있는 경우, 값을 불러와 미리 채움
    config_path = '/config/config.yaml'
    form_data = {}
    has_config = False
    
    if os.path.exists(config_path):
        try:
            # 기존 설정 파일 로드
            with open(config_path, 'r', encoding='utf-8') as f:
                yaml_config = yaml.safe_load(f)
                has_config = True
                
            # 폼 초기 데이터 준비
            form_data = {
                'PROXMOX_HOST': yaml_config.get('credentials', {}).get('proxmox_host', ''),
                'TOKEN_ID': yaml_config.get('credentials', {}).get('token_id', ''),
                'SECRET': yaml_config.get('credentials', {}).get('secret', ''),
                'NODE_NAME': yaml_config.get('proxmox', {}).get('node_name', ''),
                'VM_ID': yaml_config.get('proxmox', {}).get('vm_id', ''),
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
                'SMB_GUEST_OK': yaml_config.get('smb', {}).get('guest_ok', 'no'),
                'SMB_READ_ONLY': yaml_config.get('smb', {}).get('read_only', 'yes'),
                'SMB_LINKS_DIR': yaml_config.get('smb', {}).get('links_dir', '/mnt/gshare_links'),
                'TIMEZONE': yaml_config.get('timezone', 'Asia/Seoul')
            }
            logging.info("기존 설정 파일 로드 성공")
        except Exception as e:
            logging.error(f"설정 파일 로드 중 오류 발생: {e}")
    
    logging.info("랜딩 페이지 표시")
    return render_template('landing.html', form_data=form_data, has_config=has_config, container_ip=container_ip)

@app.route('/save-config', methods=['POST'])
def save_config():
    """설정 저장"""
    global is_setup_complete
    global is_landing_only_mode
    
    # 설정 파일 경로 정의
    config_path = '/config/config.yaml'
    init_flag_path = '/config/.init_complete'
    
    # 저장 모드 기록
    original_landing_mode = is_landing_only_mode
    
    logging.info(f"설정 저장 시작 - 원래 랜딩 모드: {original_landing_mode}")
    
    try:
        # 폼 데이터 가져오기
        form_data = request.form.to_dict()
        
        # 모든 설정을 YAML 파일로 통합
        config_dict = {
            # Proxmox 관련 설정
            'PROXMOX_HOST': form_data.get('PROXMOX_HOST', ''),
            'NODE_NAME': form_data.get('NODE_NAME', ''),
            'VM_ID': form_data.get('VM_ID', ''),
            'TOKEN_ID': form_data.get('TOKEN_ID', ''),
            'SECRET': form_data.get('SECRET', ''),
            'CPU_THRESHOLD': form_data.get('CPU_THRESHOLD', 10),
            'CHECK_INTERVAL': form_data.get('CHECK_INTERVAL', 60),
            'THRESHOLD_COUNT': form_data.get('THRESHOLD_COUNT', 3),
            
            # 마운트 관련 설정
            'MOUNT_PATH': form_data.get('MOUNT_PATH', ''),
            'GET_FOLDER_SIZE_TIMEOUT': form_data.get('GET_FOLDER_SIZE_TIMEOUT', 30),
            'NFS_PATH': form_data.get('NFS_PATH', ''),
            
            # 웹훅 설정
            'SHUTDOWN_WEBHOOK_URL': form_data.get('SHUTDOWN_WEBHOOK_URL', ''),
            
            # SMB 관련 설정
            'SMB_USERNAME': form_data.get('SMB_USERNAME', ''),
            'SMB_PASSWORD': form_data.get('SMB_PASSWORD', ''),
            'SMB_SHARE_NAME': form_data.get('SMB_SHARE_NAME', 'gshare'),
            'SMB_COMMENT': form_data.get('SMB_COMMENT', 'GShare SMB 공유'),
            'SMB_GUEST_OK': form_data.get('SMB_GUEST_OK', 'no'),
            'SMB_READ_ONLY': form_data.get('SMB_READ_ONLY', 'yes'),
            'SMB_LINKS_DIR': form_data.get('SMB_LINKS_DIR', '/mnt/gshare_links'),
            
            # 기타 설정
            'TIMEZONE': form_data.get('TIMEZONE', 'Asia/Seoul'),
            
            # 저장 시간
            'SAVE_TIME': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # 설정 파일 업데이트
        Config.update_yaml_config(config_dict)
        
        # config.yaml 파일의 수정 시간 확인
        try:
            config_mtime = os.path.getmtime(config_path)
            config_time = datetime.fromtimestamp(config_mtime)
            logging.info(f"설정 파일 업데이트 시간: {config_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # config.yaml 파일이 수정된 후 1초 대기하여 타임스탬프 차이 보장
            time.sleep(1)
            
            # 초기화 완료 플래그 생성 (설정 파일보다 나중 시간으로)
            current_time = datetime.now()
            with open(init_flag_path, 'w') as f:
                time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
                f.write(time_str)
            logging.info(f"초기화 완료 플래그 생성 시간: {time_str}")
            
            # 두 파일의 시간 차이 확인 및 로깅
            time_diff = (current_time - config_time).total_seconds()
            logging.info(f"설정 파일과 초기화 플래그 사이 시간 차이: {time_diff:.2f}초")
            
            if time_diff < 0:
                logging.warning("초기화 플래그가 설정 파일보다 이른 시간으로 설정되었습니다. 문제가 발생할 수 있습니다.")
        except Exception as e:
            logging.error(f"파일 시간 설정 중 오류 발생: {e}")
            # 오류가 발생해도 기본 동작은 계속 진행
            with open(init_flag_path, 'w') as f:
                f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        # 설정이 완료되었으므로 플래그 설정
        is_setup_complete = True
        
        logging.info(f"설정 저장 완료 - 설정 완료 페이지로 이동합니다.")
        
        # 설정 완료 상태 다시 확인
        is_setup_complete = check_setup_complete()
        logging.info(f"설정 완료 상태 재확인: {is_setup_complete}")
        
        # 항상 설정 완료 페이지로 이동
        # 설정 완료 페이지에서 자동 재시작 후 원래 모드로 돌아가도록 함
        return render_template('setup_complete.html')
    
    except Exception as e:
        logging.error(f"설정 저장 중 오류 발생: {e}")
        return render_template('landing.html', error=str(e), form_data=request.form)

@app.route('/settings')
def show_settings():
    return render_template('settings.html')

@app.route('/update_state')
def update_state():
    state = current_state if current_state is not None else get_default_state()
    return jsonify(state.to_dict())

@app.route('/update_log')
def update_log():
    if os.path.exists(log_file):
        with open(log_file, 'r') as file:
            log_content = file.read()
            return log_content
    else:
        return "Log file not found.", 404

@app.route('/restart_service')
def restart_service():
    try:
        # 도커 환경에서는 systemctl 대신 직접 프로세스 재시작
        logging.info("서비스 재시작 요청 - 도커 환경에서는 앱 재시작 함수 호출")
        
        # 프로세스 재시작
        restart_thread = threading.Thread(target=delayed_restart)
        restart_thread.daemon = True
        restart_thread.start()
        
        return jsonify({"status": "success", "message": "서비스가 재시작되었습니다."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"서비스 재시작 실패: {str(e)}"}), 500

@app.route('/retry_mount')
def retry_mount():
    try:
        # 도커 환경에서는 마운트 시도 후 앱 재시작
        try:
            # NFS 마운트 시도 (sudo 없이)
            subprocess.run(['mount', config.MOUNT_PATH], check=True)
            
            # 서비스 재시작 (systemctl 대신 직접 재시작)
            restart_thread = threading.Thread(target=delayed_restart)
            restart_thread.daemon = True
            restart_thread.start()
            
            return jsonify({"status": "success", "message": "마운트 재시도 및 서비스를 재시작했습니다."})
        except Exception as e:
            return jsonify({"status": "error", "message": f"설정 로드 또는 마운트 재시도 실패: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"마운트 재시도 실패: {str(e)}"}), 500

@app.route('/clear_log')
def clear_log():
    try:
        with open(log_file, 'w') as file:
            file.truncate(0)
        return jsonify({"status": "success", "message": "로그가 성공적으로 삭제되었습니다."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"로그 삭제 실패: {str(e)}"}), 500

@app.route('/trim_log/<int:lines>')
def trim_log(lines):
    try:
        with open(log_file, 'r') as file:
            log_lines = file.readlines()
        
        trimmed_lines = log_lines[-lines:] if len(log_lines) > lines else log_lines
        
        with open(log_file, 'w') as file:
            file.writelines(trimmed_lines)
            
        return jsonify({
            "status": "success", 
            "message": f"로그가 마지막 {lines}줄만 남도록 정리되었습니다.",
            "total_lines": len(trimmed_lines)
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"로그 정리 실패: {str(e)}"}), 500

@app.route('/set_log_level/<string:level>')
def set_log_level(level):
    try:
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        
        if level.upper() not in valid_levels:
            return jsonify({
                "status": "error",
                "message": f"유효하지 않은 로그 레벨입니다. 가능한 레벨: {', '.join(valid_levels)}"
            }), 400

        set_key('.env', 'LOG_LEVEL', level.upper())
        
        return jsonify({
            "status": "success",
            "message": f"로그 레벨이 {level.upper()}로 변경되었습니다. 다음 모니터링 루프에서 적용됩니다."
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"로그 레벨 변경 실패: {str(e)}"
        }), 500

@app.route('/get_log_level')
def get_log_level():
    try:
        load_dotenv()
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

@app.route('/start_vm')
def start_vm():
    try:
        if current_state is None:
            return jsonify({"status": "error", "message": "State not initialized."}), 404

        if current_state.vm_running:
            return jsonify({"status": "error", "message": "VM이 이미 실행 중입니다."}), 400

        if gshare_manager.proxmox_api.start_vm():
            return jsonify({"status": "success", "message": "VM 시작이 요청되었습니다."})
        else:
            return jsonify({"status": "error", "message": "VM 시작 실패"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"VM 시작 요청 실패: {str(e)}"}), 500

@app.route('/shutdown_vm')
def shutdown_vm():
    try:
        if current_state is None:
            return jsonify({"status": "error", "message": "State not initialized."}), 404

        if not current_state.vm_running:
            return jsonify({"status": "error", "message": "VM이 이미 종료되어 있습니다."}), 400
        
        response = requests.post(config.SHUTDOWN_WEBHOOK_URL, timeout=5)
        response.raise_for_status()
        return jsonify({"status": "success", "message": "VM 종료가 요청되었습니다."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"VM 종료 요청 실패: {str(e)}"}), 500

@app.route('/toggle_mount/<path:folder>')
def toggle_mount(folder):
    try:
        if current_state is None:
            return jsonify({"status": "error", "message": "State not initialized."}), 404

        if folder in gshare_manager.folder_monitor.active_links:
            # 마운트 해제
            if gshare_manager.folder_monitor._remove_symlink(folder):
                # 상태 즉시 업데이트
                gshare_manager._update_state()
                return jsonify({"status": "success", "message": f"{folder} 마운트가 해제되었습니다."})
            else:
                return jsonify({"status": "error", "message": f"{folder} 마운트 해제 실패"}), 500
        else:
            # 마운트
            if gshare_manager.folder_monitor._create_symlink(folder) and gshare_manager.folder_monitor._activate_smb_share():
                # 상태 즉시 업데이트
                gshare_manager._update_state()
                return jsonify({"status": "success", "message": f"{folder} 마운트가 활성화되었습니다."})
            else:
                return jsonify({"status": "error", "message": f"{folder} 마운트 활성화 실패"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"마운트 상태 변경 실패: {str(e)}"}), 500

@app.route('/get_config')
def get_config():
    """설정 정보 제공"""
    if not config:
        return jsonify({"error": "설정이 로드되지 않았습니다."}), 500
    
    # 설정 파일 경로
    yaml_path = '/config/config.yaml'
    
    try:
        # YAML 파일에서 직접 설정 로드
        if os.path.exists(yaml_path):
            with open(yaml_path, 'r', encoding='utf-8') as f:
                yaml_config = yaml.safe_load(f)
        else:
            yaml_config = {}
        
        # 설정 데이터 준비
        config_data = {
            'PROXMOX_HOST': yaml_config.get('credentials', {}).get('proxmox_host', ''),
            'NODE_NAME': yaml_config.get('proxmox', {}).get('node_name', ''),
            'VM_ID': yaml_config.get('proxmox', {}).get('vm_id', ''),
            'CPU_THRESHOLD': yaml_config.get('proxmox', {}).get('cpu', {}).get('threshold', 10),
            'CHECK_INTERVAL': yaml_config.get('proxmox', {}).get('cpu', {}).get('check_interval', 60),
            'THRESHOLD_COUNT': yaml_config.get('proxmox', {}).get('cpu', {}).get('threshold_count', 3),
            'MOUNT_PATH': yaml_config.get('mount', {}).get('path', ''),
            'GET_FOLDER_SIZE_TIMEOUT': yaml_config.get('mount', {}).get('folder_size_timeout', 30),
            'NFS_PATH': yaml_config.get('nfs', {}).get('path', ''),
            'SHUTDOWN_WEBHOOK_URL': yaml_config.get('credentials', {}).get('shutdown_webhook_url', ''),
            'SMB_SHARE_NAME': yaml_config.get('smb', {}).get('share_name', 'gshare'),
            'SMB_COMMENT': yaml_config.get('smb', {}).get('comment', 'GShare SMB 공유'),
            'SMB_GUEST_OK': 'yes' if yaml_config.get('smb', {}).get('guest_ok', False) else 'no',
            'SMB_READ_ONLY': 'yes' if yaml_config.get('smb', {}).get('read_only', True) else 'no',
            'SMB_LINKS_DIR': yaml_config.get('smb', {}).get('links_dir', '/mnt/gshare_links'),
            'TIMEZONE': yaml_config.get('timezone', 'Asia/Seoul')
        }
        
        # 민감한 정보는 마스킹 처리
        if 'TOKEN_ID' in yaml_config.get('credentials', {}):
            config_data['TOKEN_ID'] = yaml_config['credentials']['token_id']
        if 'SECRET' in yaml_config.get('credentials', {}):
            config_data['SECRET'] = '********'  # 보안을 위해 실제값 대신 마스킹
        if 'SMB_USERNAME' in yaml_config.get('credentials', {}):
            config_data['SMB_USERNAME'] = yaml_config['credentials']['smb_username']
        if 'SMB_PASSWORD' in yaml_config.get('credentials', {}):
            config_data['SMB_PASSWORD'] = '********'  # 보안을 위해 실제값 대신 마스킹
        
        return jsonify(config_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/update_config', methods=['POST'])
def update_config():
    """설정 업데이트"""
    try:
        data = request.json
        
        # YAML 설정 업데이트
        Config.update_yaml_config(data)
        
        return jsonify({
            "status": "success",
            "message": "설정이 업데이트되었습니다. 변경사항을 적용하려면 서비스를 재시작하세요."
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/test_proxmox_api', methods=['POST'])
def test_proxmox_api():
    """Proxmox API 연결 테스트"""
    try:
        # POST 데이터에서 필요한 정보 추출
        proxmox_host = request.form.get('proxmox_host')
        node_name = request.form.get('node_name')
        vm_id = request.form.get('vm_id')
        token_id = request.form.get('token_id')
        secret = request.form.get('secret')
        
        if not all([proxmox_host, node_name, vm_id, token_id, secret]):
            return jsonify({
                "status": "error",
                "message": "필수 항목이 누락되었습니다."
            }), 400
        
        # 임시 세션 생성
        session = requests.Session()
        session.verify = False
        session.headers.update({
            "Authorization": f"PVEAPIToken={token_id}={secret}"
        })
        session.timeout = (5, 10)  # (연결 타임아웃, 읽기 타임아웃)
        
        try:
            # API 버전 정보 가져오기 (기본 연결 테스트)
            version_response = session.get(f"{proxmox_host}/version")
            version_response.raise_for_status()
            
            # 노드 정보 가져오기 (노드 존재 테스트)
            node_response = session.get(f"{proxmox_host}/nodes/{node_name}")
            node_response.raise_for_status()
            
            # VM 상태 확인 (VM 접근 테스트)
            vm_response = session.get(f"{proxmox_host}/nodes/{node_name}/qemu/{vm_id}/status/current")
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

@app.route('/test_nfs', methods=['POST'])
def test_nfs():
    """NFS 연결 테스트"""
    try:
        nfs_path = request.form.get('nfs_path')
        if not nfs_path:
            return jsonify({
                "status": "error",
                "message": "NFS 경로가 제공되지 않았습니다."
            }), 400
        
        # 임시 마운트 포인트 생성
        with tempfile.TemporaryDirectory() as temp_mount:
            try:
                # NFS 마운트 시도 (여러 옵션 추가)
                mount_cmd = f"mount -t nfs -o nolock,vers=3,soft,timeo=100 {nfs_path} {temp_mount}"
                result = subprocess.run(mount_cmd, shell=True, capture_output=True, text=True, timeout=60)
                
                if result.returncode == 0:
                    # 마운트 성공 - 읽기 테스트
                    try:
                        # 읽기 테스트
                        ls_result = subprocess.run(f"ls {temp_mount}", shell=True, check=True, capture_output=True, text=True)
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
                # 마운트 해제 시도
                try:
                    subprocess.run(f"umount {temp_mount}", shell=True, check=False)
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

@app.route('/import-config', methods=['POST'])
def import_config():
    """업로드된 설정 파일 가져오기"""
    try:
        logging.info("설정 파일 업로드 요청 처리 중...")
        
        # 파일이 업로드되었는지 확인
        if 'config_file' not in request.files:
            logging.warning("업로드된 파일이 없습니다.")
            return jsonify({
                "status": "error",
                "message": "업로드된 파일이 없습니다."
            }), 400
        
        file = request.files['config_file']
        
        # 파일명 확인
        if file.filename == '':
            logging.warning("선택된 파일이 없습니다.")
            return jsonify({
                "status": "error",
                "message": "선택된 파일이 없습니다."
            }), 400
        
        # 확장자 확인
        if not (file.filename.endswith('.yaml') or file.filename.endswith('.yml')):
            logging.warning(f"잘못된 파일 형식: {file.filename}")
            return jsonify({
                "status": "error",
                "message": "YAML 파일(.yaml 또는 .yml)만 업로드 가능합니다."
            }), 400
        
        # 파일 내용 읽기
        try:
            file_content = file.read().decode('utf-8')
            yaml_config = yaml.safe_load(file_content)
            
            if not yaml_config:
                logging.warning("빈 YAML 파일이 업로드되었습니다.")
                return jsonify({
                    "status": "error",
                    "message": "업로드된 파일이 비어있거나 유효한 YAML 형식이 아닙니다."
                }), 400
            
            # 기본 구조 확인
            required_sections = ['credentials', 'proxmox']
            missing_sections = [section for section in required_sections if section not in yaml_config]
            
            if missing_sections:
                logging.warning(f"필수 섹션 누락: {missing_sections}")
                return jsonify({
                    "status": "warning",
                    "message": f"설정 파일에 필수 섹션이 누락되었습니다: {', '.join(missing_sections)}"
                }), 200
            
            # 필수 자격 증명 확인
            required_credentials = ['proxmox_host', 'token_id', 'secret']
            missing_credentials = []
            
            for cred in required_credentials:
                if cred not in yaml_config['credentials'] or not yaml_config['credentials'][cred]:
                    missing_credentials.append(cred)
            
            if missing_credentials:
                logging.warning(f"필수 자격 증명이 누락되었습니다: {missing_credentials}")
                return jsonify({
                    "status": "warning",
                    "message": f"필수 자격 증명이 누락되었습니다: {', '.join(missing_credentials)}"
                }), 200
            
            # Form 데이터 구성
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
                'TIMEZONE': yaml_config.get('timezone', 'Asia/Seoul')
            }
            
            # 설정 파일을 임시로 저장 (옵션)
            temp_config_path = '/tmp/imported_config.yaml'
            try:
                with open(temp_config_path, 'w') as f:
                    yaml.dump(yaml_config, f, allow_unicode=True, default_flow_style=False)
                logging.info(f"가져온 설정을 임시 파일에 저장했습니다: {temp_config_path}")
            except Exception as e:
                logging.error(f"임시 설정 파일 저장 실패: {e}")
            
            logging.info("설정 파일 가져오기 성공")
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

@app.route('/export-config')
def export_config():
    """현재 설정 파일 내보내기"""
    try:
        config_path = '/config/config.yaml'
        
        # 설정 파일이 존재하는지 확인
        if not os.path.exists(config_path):
            logging.warning("내보낼 설정 파일이 없습니다.")
            return jsonify({
                "status": "error",
                "message": "설정 파일이 없습니다."
            }), 404
        
        # 설정 파일 읽기
        with open(config_path, 'r', encoding='utf-8') as f:
            yaml_config = yaml.safe_load(f)
            
        if not yaml_config:
            logging.warning("설정 파일이 비어있습니다.")
            return jsonify({
                "status": "error",
                "message": "설정 파일이 비어있습니다."
            }), 400
        
        # 설정 파일 내용을 YAML 형식으로 변환
        yaml_content = yaml.dump(yaml_config, allow_unicode=True, default_flow_style=False)
        
        # 날짜를 포함한 파일명 생성
        current_time = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"gshare_config_{current_time}.yaml"
        
        # 응답 생성
        response = app.response_class(
            response=yaml_content,
            status=200,
            mimetype='application/x-yaml'
        )
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        
        logging.info("설정 파일 내보내기 성공")
        return response
        
    except Exception as e:
        logging.error(f"설정 파일 내보내기 중 오류 발생: {e}")
        return jsonify({
            "status": "error",
            "message": f"설정 파일 내보내기 중 오류 발생: {str(e)}"
        }), 500

@app.route('/restart_app', methods=['POST'])
def restart_app():
    """앱 재시작 엔드포인트"""
    try:
        logging.info("──────────────────────────────────────────────────")
        logging.info("앱 재시작 요청 받음")
        logging.info("──────────────────────────────────────────────────")
        
        # config.yaml 파일의 존재 여부와 수정 시간 확인
        config_path = '/config/config.yaml'
        init_flag_path = '/config/.init_complete'
        
        if not os.path.exists(config_path):
            logging.warning("설정 파일이 존재하지 않습니다. 재시작할 수 없습니다.")
            return jsonify({"status": "error", "message": "설정 파일이 존재하지 않습니다."}), 400
        
        # 설정 파일의 수정 시간 확인
        try:
            config_mtime = os.path.getmtime(config_path)
            config_time = datetime.fromtimestamp(config_mtime)
            logging.info(f"설정 파일 수정 시간: {config_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 초기화 완료 플래그 확인 및 업데이트
            if not os.path.exists(init_flag_path):
                logging.info("초기화 완료 플래그가 없습니다. 새로 생성합니다.")
                # 1초 지연을 통해 설정 파일보다 나중 시간으로 생성
                time.sleep(1)
            else:
                # 기존 플래그의 시간 확인
                try:
                    with open(init_flag_path, 'r') as f:
                        init_time_str = f.read().strip()
                    init_time = datetime.strptime(init_time_str, '%Y-%m-%d %H:%M:%S')
                    
                    # 플래그 시간이 설정 파일보다 이전이면 업데이트 필요
                    if init_time <= config_time:
                        logging.info("초기화 플래그 시간이 설정 파일보다 이전입니다. 업데이트합니다.")
                        time.sleep(1)  # 1초 지연
                    else:
                        logging.info("초기화 플래그 시간이 이미 최신입니다.")
                        # 업데이트 없이 진행
                        pass
                except Exception as e:
                    logging.error(f"초기화 플래그 시간 확인 중 오류: {e}")
                    # 오류 발생 시 안전하게 업데이트
                    time.sleep(1)
            
            # 초기화 완료 플래그 생성 또는 업데이트
            current_time = datetime.now()
            with open(init_flag_path, 'w') as f:
                time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
                f.write(time_str)
            logging.info(f"초기화 완료 플래그 업데이트 시간: {time_str}")
            
            # 시간 차이 확인
            time_diff = (current_time - config_time).total_seconds()
            logging.info(f"설정 파일과 초기화 플래그 사이 시간 차이: {time_diff:.2f}초")
            
            if time_diff < 0:
                logging.warning("초기화 플래그가 설정 파일보다 이른 시간으로 설정되었습니다. 문제가 발생할 수 있습니다.")
                # 다시 시도
                time.sleep(2)
                current_time = datetime.now()
                with open(init_flag_path, 'w') as f:
                    time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
                    f.write(time_str)
                logging.info(f"초기화 완료 플래그 재업데이트 시간: {time_str}")
        except Exception as e:
            logging.error(f"초기화 완료 플래그 업데이트 중 오류: {e}")
            # 오류 발생 시에도 기본 동작 진행
            with open(init_flag_path, 'w') as f:
                f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        # 설정 완료 여부 확인 로직 제거 - 재시작 시 자동으로 설정 상태 확인
        logging.info("앱 재시작을 진행합니다. 재시작 후 애플리케이션이 자체적으로 설정 상태 확인")
        
        # delayed_restart 함수를 사용하여 앱 재시작
        restart_thread = threading.Thread(target=delayed_restart)
        restart_thread.daemon = True
        restart_thread.start()
        
        logging.info("앱 재시작 요청 처리 완료")
        return jsonify({"status": "success", "message": "앱이 재시작됩니다."})
    except Exception as e:
        logging.error(f"앱 재시작 중 오류 발생: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def run_landing_page():
    """랜딩 페이지 전용 웹서버 실행"""
    try:
        logging.info("──────────────────────────────────────────────────")
        logging.info("랜딩 페이지 모드로 웹 서버 시작")
        logging.info("──────────────────────────────────────────────────")
        run_flask_app()
    except Exception as e:
        logging.error(f"랜딩 페이지 웹 서버 실행 중 오류 발생: {e}")
        logging.error(f"상세 오류: {traceback.format_exc()}")
        sys.exit(1)

def run_flask_app():
    """Flask 웹 애플리케이션 실행"""
    try:
        logging.info("Flask 웹 서버 시작 중...")
        host = '0.0.0.0'
        # 환경 변수에서 포트 설정 확인 (기본값: 5000)
        port = int(os.environ.get('FLASK_PORT', 5000))
        logging.info(f"웹 서버 바인딩: {host}:{port}")
        
        # 소켓 설정 - 포트 재사용 허용으로 수정
        from werkzeug.serving import make_server, BaseWSGIServer
        import socket
        
        # 소켓 옵션 설정을 위한 원래 메서드 백업
        original_server_bind = BaseWSGIServer.server_bind
        
        # 소켓 옵션을 설정하는 메서드 오버라이드
        def custom_server_bind(self):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            return original_server_bind(self)
        
        # 메서드 교체
        BaseWSGIServer.server_bind = custom_server_bind
        
        # 서버 실행
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except OSError as e:
        if e.errno == 98:  # 주소 이미 사용 중
            logging.warning(f"포트 {port} 이미 사용 중: 다른 포트로 시도합니다.")
            # 다른 포트로 시도
            os.environ['FLASK_PORT'] = str(port + 1)
            return run_flask_app()  # 재시도
        else:
            logging.error(f"Flask 웹 서버 실행 중 오류 발생: {e}")
            logging.error(f"상세 오류: {traceback.format_exc()}")
            time.sleep(10)  # 10초 대기 후 재시도
            return run_flask_app()  # 재시도
    except Exception as e:
        logging.error(f"Flask 웹 서버 실행 중 오류 발생: {e}")
        logging.error(f"상세 오류: {traceback.format_exc()}")
        # 에러가 발생하더라도 전체 애플리케이션 종료는 방지
        time.sleep(10)  # 10초 대기 후 재시도
        logging.info("Flask 웹 서버 재시작 시도...")
        return run_flask_app()  # 재귀적으로 다시 시도

# 앱 재시작을 위한 함수 (모든 systemctl 호출 대체)
def delayed_restart():
    time.sleep(2)  # 응답을 보낼 시간을 주기 위해 잠시 대기
    logging.info("──────────────────────────────────────────────────")
    logging.info("앱 재시작 실행 중...")
    logging.info("──────────────────────────────────────────────────")
    
    try:
        # 포트 5000을 사용 중인 프로세스 확인 및 종료 시도
        try:
            # 도커 환경에서 포트 5000을 사용하는 프로세스 찾기
            logging.info("포트 5000 사용중인 프로세스 확인...")
            
            # lsof 명령이 있는지 확인
            lsof_exists = subprocess.run(
                ["which", "lsof"], 
                capture_output=True,
                check=False
            ).returncode == 0
            
            if lsof_exists:
                # lsof로 포트 사용 프로세스 찾기
                result = subprocess.run(
                    ["lsof", "-i", ":5000"],
                    capture_output=True, 
                    text=True,
                    check=False
                )
                if result.stdout:
                    processes = result.stdout.strip().split('\n')[1:]  # 헤더 제외
                    for process in processes:
                        parts = process.split()
                        if len(parts) > 1:
                            pid = parts[1]
                            try:
                                logging.info(f"포트 5000 사용 프로세스(PID: {pid}) 종료 시도...")
                                subprocess.run(["kill", pid], check=False)
                            except Exception as e:
                                logging.warning(f"프로세스 종료 중 오류: {e}")
            else:
                # lsof가 없는 경우 대체 명령 사용
                logging.info("lsof 명령이 없습니다. 대체 명령 사용...")
                try:
                    # ss 또는 netstat으로 시도
                    ss_exists = subprocess.run(
                        ["which", "ss"], 
                        capture_output=True,
                        check=False
                    ).returncode == 0
                    
                    if ss_exists:
                        # ss 명령 사용
                        result = subprocess.run(
                            ["ss", "-tunlp", "sport = :5000"],
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        if "pid=" in result.stdout:
                            # pid 추출 (형식: pid=12345,...)
                            import re
                            pid_matches = re.findall(r'pid=(\d+)', result.stdout)
                            for pid in pid_matches:
                                logging.info(f"ss로 찾은 포트 5000 사용 프로세스(PID: {pid}) 종료 시도...")
                                subprocess.run(["kill", pid], check=False)
                    else:
                        # netstat 시도
                        netstat_exists = subprocess.run(
                            ["which", "netstat"], 
                            capture_output=True,
                            check=False
                        ).returncode == 0
                        
                        if netstat_exists:
                            result = subprocess.run(
                                ["netstat", "-tunlp"],
                                capture_output=True,
                                text=True,
                                check=False
                            )
                            for line in result.stdout.split('\n'):
                                if ":5000" in line and "LISTEN" in line:
                                    # PID 추출 (형식: PID/프로그램명)
                                    parts = line.split()
                                    for part in parts:
                                        if '/' in part:
                                            pid = part.split('/')[0]
                                            logging.info(f"netstat으로 찾은 포트 5000 사용 프로세스(PID: {pid}) 종료 시도...")
                                            subprocess.run(["kill", pid], check=False)
                except Exception as e:
                    logging.warning(f"대체 명령 사용 중 오류: {e}")
                    
            # Python 기반 방식으로 포트 사용 여부 확인
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', 5000))
                sock.close()
                
                if result == 0:  # 포트가 열려 있음
                    logging.warning("포트 5000이 여전히 사용 중입니다. python 프로세스 종료 시도...")
                    # 현재 PID 제외한 모든 Python 프로세스 종료 시도
                    current_pid = os.getpid()
                    try:
                        result = subprocess.run(
                            ["ps", "-ef"], 
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        for line in result.stdout.split('\n'):
                            if 'python' in line and 'gshare_manager.py' in line:
                                parts = line.split()
                                if len(parts) > 1:
                                    pid = parts[1]
                                    if pid and pid.isdigit() and int(pid) != current_pid:
                                        logging.info(f"다른 Python 프로세스(PID: {pid}) 종료 시도...")
                                        subprocess.run(["kill", pid], check=False)
                    except Exception as e:
                        logging.warning(f"Python 프로세스 종료 시도 중 오류: {e}")
            except Exception as e:
                logging.warning(f"소켓 확인 중 오류: {e}")
                
            # 프로세스가 종료될 때까지 잠시 대기
            time.sleep(3)
        except Exception as e:
            logging.warning(f"프로세스 확인/종료 중 오류: {e}")
        
        # 현재 실행 경로 확인
        current_path = os.path.abspath(os.path.dirname(sys.argv[0]))
        logging.info(f"현재 실행 경로: {current_path}")
        
        main_script = os.path.join(current_path, 'gshare_manager.py')
        
        # 파일이 발견되지 않은 경우
        if not os.path.exists(main_script):
            logging.error("메인 스크립트를 찾을 수 없음!")
            # 현재 디렉토리와 앱 디렉토리 목록 출력
            logging.info(f"현재 디렉토리: {current_path}")
            logging.info(f"현재 디렉토리 파일 목록: {os.listdir('.')}")
            if os.path.exists('app'):
                logging.info(f"app 디렉토리 파일 목록: {os.listdir('app')}")
            
            # 기본값으로 시도
            main_script = 'app/gshare_manager.py'
            logging.info(f"기본 경로로 시도: {main_script}")
        
        logging.info(f"앱 재시작 실행 명령: {sys.executable} {main_script}")
        
        # Python 인터프리터로 메인 스크립트 실행 - 환경 변수 설정 제거
        env = os.environ.copy()
        
        # 재시작 전에 잠시 대기하여 포트가 해제될 수 있도록 함
        time.sleep(3)
        
        # 추가 안전장치: 포트가 여전히 사용 중인지 확인
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('0.0.0.0', 5000))
            sock.close()
            if result == 0:  # 포트가 열려 있음 (사용 중)
                logging.warning("포트 5000이 여전히 사용 중입니다. 추가 대기...")
                time.sleep(5)  # 추가 대기
                
                # 비상 수단: 포트가 계속 사용 중이면 강제로 다른 포트 사용
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('0.0.0.0', 5000))
                sock.close()
                
                if result == 0:  # 여전히 사용 중
                    logging.warning("포트 5000이 계속 사용 중입니다. 환경 변수로 다른 포트 설정...")
                    env['FLASK_PORT'] = '5001'  # 다른 포트로 설정
        except Exception as e:
            logging.warning(f"포트 확인 중 오류: {e}")
        
        # -u 옵션을 추가하여 버퍼링 없이 로그 출력
        subprocess.Popen(
            [sys.executable, "-u", main_script], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0  # 버퍼링 없음
        )
        
        # 현재 프로세스는 잠시 후 종료
        def exit_current_process():
            # 종료 전에 더 오래 대기 (포트 해제를 위해)
            time.sleep(5)  # 5초로 증가
            logging.info("──────────────────────────────────────────────────")
            logging.info("현재 프로세스를 종료합니다...")
            logging.info("──────────────────────────────────────────────────")
            os._exit(0)
        
        exit_thread = threading.Thread(target=exit_current_process)
        exit_thread.daemon = True
        exit_thread.start()
        
    except Exception as e:
        logging.error(f"앱 재시작 중 오류 발생: {e}")

if __name__ == "__main__":
    # 랜딩 페이지 전용 모드인 경우
    if is_landing_only_mode:
        logging.info("랜딩 페이지 전용 모드로 실행 중...")
        is_setup_complete = False
        run_flask_app()
    else:
        # 일반 모드에서는 gshare_manager.py에서 호출하는 방식으로 작동
        logging.info("일반 모드에서는 gshare_manager.py에서 호출됩니다.")
        pass 