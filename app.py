from flask import Flask, jsonify, render_template
import os
import json
from datetime import datetime
import pytz
import subprocess
import logging
from dotenv import load_dotenv, set_key
import requests
from config import Config
from gshare_manager import ProxmoxAPI  # ProxmoxAPI 클래스 import

app = Flask(__name__)

LOG_FILE_PATH = 'gshare_manager.log'
STATE_FILE_PATH = 'current_state.json'

# ProxmoxAPI 인스턴스 생성
config = Config()
proxmox_api = ProxmoxAPI(config)

def get_time_ago(timestamp_str):
    try:
        seoul_tz = pytz.timezone('Asia/Seoul')
        last_check = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        last_check = seoul_tz.localize(last_check)
        now = datetime.now(seoul_tz)
        
        diff = now - last_check
        seconds = diff.total_seconds()
        
        time_ago = ""
        if seconds < 150:
            time_ago = f"{int(seconds)}초 전"
        elif seconds < 3600:  # 1시간
            time_ago = f"{int(seconds / 60)}분 전"
        elif seconds < 86400:  # 1일
            time_ago = f"{int(seconds / 3600)}시간 전"
        else:
            time_ago = f"{int(seconds / 86400)}일 전"
            
        return time_ago
    except:
        return timestamp_str

@app.route('/')
def show_log():
    # 먼저 로그 파일을 읽습니다
    log_content = ""
    if os.path.exists(LOG_FILE_PATH):
        with open(LOG_FILE_PATH, 'r') as file:
            log_content = file.read()
    else:
        return "Log file not found.", 404

    # 그 다음 상태 정보를 읽고 HTML을 생성합니다
    if os.path.exists(STATE_FILE_PATH):
        with open(STATE_FILE_PATH, 'r') as file:
            state = json.load(file)
            # folder_size_readable이 없는 이전 버전의 상태 파일 처리
            if 'folder_size_readable' not in state and 'folder_size' in state:
                from gshare_manager import format_size
                state['folder_size_readable'] = format_size(state['folder_size'])
            return render_template('index.html', 
                                state=state, 
                                log_content=log_content, 
                                get_time_ago=get_time_ago)
    
    return "State file not found.", 404

@app.route('/update_state')
def update_state():
    if os.path.exists(STATE_FILE_PATH):
        with open(STATE_FILE_PATH, 'r') as file:
            state = json.load(file)
            # folder_size_readable이 없는 이전 버전의 상태 파일 처리
            if 'folder_size_readable' not in state and 'folder_size' in state:
                from gshare_manager import format_size
                state['folder_size_readable'] = format_size(state['folder_size'])
            return jsonify(state)
    return jsonify({"error": "State file not found."}), 404

@app.route('/update_log')
def update_log():
    if os.path.exists(LOG_FILE_PATH):
        with open(LOG_FILE_PATH, 'r') as file:
            log_content = file.read()
            return log_content
    else:
        return "Log file not found.", 404

@app.route('/restart_service')
def restart_service():
    try:
        # sudo 권한이 필요한 명령어이므로, 적절한 권한 설정이 필요합니다
        subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager.service'], check=True)
        subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager_log_server.service'], check=True)
        return jsonify({"status": "success", "message": "서비스가 재시작되었습니다."})
    except subprocess.CalledProcessError as e:
        return jsonify({"status": "error", "message": f"서비스 재시작 실패: {str(e)}"}), 500

@app.route('/clear_log')
def clear_log():
    try:
        open(LOG_FILE_PATH, 'w').close()  # 로그 파일 비우기
        return jsonify({"status": "success", "message": "로그가 성공적으로 삭제되었습니다."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"로그 삭제 실패: {str(e)}"}), 500

@app.route('/trim_log/<int:lines>')
def trim_log(lines):
    try:
        with open(LOG_FILE_PATH, 'r') as file:
            log_lines = file.readlines()
        
        # 마지막 n줄만 유지
        trimmed_lines = log_lines[-lines:] if len(log_lines) > lines else log_lines
        
        with open(LOG_FILE_PATH, 'w') as file:
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
        # 유효한 로그 레벨 확인
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        
        if level.upper() not in valid_levels:
            return jsonify({
                "status": "error",
                "message": f"유효하지 않은 로그 레벨입니다. 가능한 레벨: {', '.join(valid_levels)}"
            }), 400

        # .env 파일에 로그 레벨 저장
        set_key('.env', 'LOG_LEVEL', level.upper())
        
        # 체크 간격 가져오기
        check_interval = config.CHECK_INTERVAL
        
        return jsonify({
            "status": "success",
            "message": f"로그 레벨이 {level.upper()}로 변경되었습니다. 최대 {check_interval}초 후 다음 모니터링 루프에서 적용됩니다."
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
        level = os.getenv('LOG_LEVEL', 'INFO')  # 기본값은 INFO
            
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
        if os.path.exists(STATE_FILE_PATH):
            with open(STATE_FILE_PATH, 'r') as f:
                state = json.load(f)
                if state['vm_status'] == '🟢':
                    return jsonify({"status": "error", "message": "VM이 이미 실행 중입니다."}), 400

        # ProxmoxAPI를 사용하여 VM 시작
        if proxmox_api.start_vm():
            return jsonify({"status": "success", "message": "VM 시작이 요청되었습니다."})
        else:
            return jsonify({"status": "error", "message": "VM 시작 실패"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"VM 시작 요청 실패: {str(e)}"}), 500

@app.route('/shutdown_vm')
def shutdown_vm():
    try:
        if os.path.exists(STATE_FILE_PATH):
            with open(STATE_FILE_PATH, 'r') as f:
                state = json.load(f)
                if state['vm_status'] == '🔴':
                    return jsonify({"status": "error", "message": "VM이 이미 종료되어 있습니다."}), 400
        
        # config.py에서 SHUTDOWN_WEBHOOK_URL을 가져와서 사용
        response = requests.post(config.SHUTDOWN_WEBHOOK_URL, timeout=5)
        response.raise_for_status()
        return jsonify({"status": "success", "message": "VM 종료가 요청되었습니다."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"VM 종료 요청 실패: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000) 