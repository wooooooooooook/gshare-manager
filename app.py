from flask import Flask, jsonify, render_template
import os
import json
from datetime import datetime
import pytz
import subprocess
import logging
from dotenv import load_dotenv, set_key

app = Flask(__name__)

LOG_FILE_PATH = 'gshare_manager.log'
STATE_FILE_PATH = 'current_state.json'
def format_size(size_in_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_in_bytes < 1024:
            return f"{size_in_bytes:.3f} {unit}"
        size_in_bytes /= 1024
    return f"{size_in_bytes:.3f} TB"

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
            # format_size 함수를 템플릿에서 사용할 수 있도록 전달
            return render_template('index.html', 
                                state=state, 
                                log_content=log_content, 
                                format_size=format_size,
                                get_time_ago=get_time_ago)
    
    return "State file not found.", 404

@app.route('/update_state')
def update_state():
    if os.path.exists(STATE_FILE_PATH):
        with open(STATE_FILE_PATH, 'r') as file:
            state = json.load(file)
            return jsonify(state)  # JSON 형식으로 상태 반환
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
        valid_levels = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL
        }
        
        if level.upper() not in valid_levels:
            return jsonify({
                "status": "error",
                "message": f"유효하지 않은 로그 레벨입니다. 가능한 레벨: {', '.join(valid_levels.keys())}"
            }), 400

        # .env 파일에 로그 레벨 저장
        set_key('.env', 'LOG_LEVEL', level.upper())
            
        # gshare_manager 서비스 재시작
        subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager.service'], check=True)
        
        return jsonify({
            "status": "success",
            "message": f"로그 레벨이 {level.upper()}로 변경되었습니다."
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000) 