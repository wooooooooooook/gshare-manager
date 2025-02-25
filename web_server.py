import logging
import os
import subprocess
import requests
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv, set_key
from models import State, get_default_state
from config import Config

# Flask 앱 초기화
app = Flask(__name__)
app.logger.disabled = True
log = logging.getLogger('werkzeug')
log.disabled = True

# 전역 상태 변수
current_state = get_default_state()

@app.route('/')
def show_log():
    """로그 및 현재 상태를 보여주는 메인 페이지"""
    log_content = ""
    if os.path.exists('gshare_manager.log'):
        with open('gshare_manager.log', 'r') as file:
            log_content = file.read()
    else:
        log_content = "로그 파일을 찾을 수 없습니다."

    return render_template('index.html', 
                         state=current_state.to_dict(), 
                         log_content=log_content)

@app.route('/settings')
def show_settings():
    """설정 페이지"""
    return render_template('settings.html')

@app.route('/update_state')
def update_state():
    """현재 상태를 JSON으로 반환"""
    return jsonify(current_state.to_dict())

@app.route('/update_log')
def update_log():
    """최신 로그 내용을 반환"""
    if os.path.exists('gshare_manager.log'):
        with open('gshare_manager.log', 'r') as file:
            log_content = file.read()
            return log_content
    else:
        return "Log file not found.", 404

@app.route('/restart_service')
def restart_service():
    """서비스 재시작"""
    try:
        subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager.service'], check=True)
        subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager_log_server.service'], check=True)
        return jsonify({"status": "success", "message": "서비스가 재시작되었습니다."})
    except subprocess.CalledProcessError as e:
        return jsonify({"status": "error", "message": f"서비스 재시작 실패: {str(e)}"}), 500

@app.route('/retry_mount')
def retry_mount():
    """마운트 재시도"""
    try:
        # 설정을 다시 로드
        try:
            config = Config.load_config()
            subprocess.run(['sudo', 'mount', config.MOUNT_PATH], check=True)
            subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager.service'], check=True)
            subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager_log_server.service'], check=True)
            return jsonify({"status": "success", "message": "마운트 재시도 및 서비스를 재시작했습니다."})
        except Exception as e:
            return jsonify({"status": "error", "message": f"설정 로드 또는 마운트 재시도 실패: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"마운트 재시도 실패: {str(e)}"}), 500

@app.route('/clear_log')
def clear_log():
    """로그 파일 삭제"""
    try:
        open('gshare_manager.log', 'w').close()
        return jsonify({"status": "success", "message": "로그가 성공적으로 삭제되었습니다."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"로그 삭제 실패: {str(e)}"}), 500

@app.route('/trim_log/<int:lines>')
def trim_log(lines):
    """로그 파일 정리"""
    try:
        with open('gshare_manager.log', 'r') as file:
            log_lines = file.readlines()
        
        trimmed_lines = log_lines[-lines:] if len(log_lines) > lines else log_lines
        
        with open('gshare_manager.log', 'w') as file:
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
    """로그 레벨 설정"""
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
    """현재 로그 레벨 확인"""
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
    """VM 시작"""
    try:
        # gshare_manager 모듈에서 전역 변수 가져오기
        import gshare_manager as gm
        
        if current_state.vm_running:
            return jsonify({"status": "error", "message": "VM이 이미 실행 중입니다."}), 400

        if gm.gshare_manager.proxmox_api.start_vm():
            return jsonify({"status": "success", "message": "VM 시작이 요청되었습니다."})
        else:
            return jsonify({"status": "error", "message": "VM 시작 실패"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"VM 시작 요청 실패: {str(e)}"}), 500

@app.route('/shutdown_vm')
def shutdown_vm():
    """VM 종료"""
    try:
        # gshare_manager 모듈에서 전역 변수 가져오기
        import gshare_manager as gm
        
        if not current_state.vm_running:
            return jsonify({"status": "error", "message": "VM이 이미 종료되어 있습니다."}), 400
        
        if gm.gshare_manager.folder_monitor.send_shutdown_webhook():
            return jsonify({"status": "success", "message": "VM 종료가 요청되었습니다."})
        else:
            return jsonify({"status": "error", "message": "VM 종료 요청 실패"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"VM 종료 요청 실패: {str(e)}"}), 500

@app.route('/toggle_mount/<path:folder>')
def toggle_mount(folder):
    """폴더 마운트 상태 토글"""
    try:
        # gshare_manager 모듈에서 전역 변수 가져오기
        import gshare_manager as gm
        
        if folder in gm.gshare_manager.folder_monitor.active_links:
            # 마운트 해제
            if gm.gshare_manager.folder_monitor._remove_symlink(folder):
                # 상태 즉시 업데이트
                gm.gshare_manager._update_state()
                return jsonify({"status": "success", "message": f"{folder} 마운트가 해제되었습니다."})
            else:
                return jsonify({"status": "error", "message": f"{folder} 마운트 해제 실패"}), 500
        else:
            # 마운트
            if gm.gshare_manager.folder_monitor._create_symlink(folder) and gm.gshare_manager.folder_monitor.smb_manager.activate_smb_share(
                gm.gshare_manager.folder_monitor.nfs_uid, gm.gshare_manager.folder_monitor.nfs_gid
            ):
                # 상태 즉시 업데이트
                gm.gshare_manager._update_state()
                return jsonify({"status": "success", "message": f"{folder} 마운트가 활성화되었습니다."})
            else:
                return jsonify({"status": "error", "message": f"{folder} 마운트 활성화 실패"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"마운트 상태 변경 실패: {str(e)}"}), 500

@app.route('/get_config')
def get_config():
    """현재 설정 가져오기"""
    try:
        config = Config.load_config()
        config_dict = {
            'PROXMOX_HOST': config.PROXMOX_HOST,
            'NODE_NAME': config.NODE_NAME,
            'VM_ID': config.VM_ID,
            'TOKEN_ID': config.TOKEN_ID,
            'SECRET': '********',  # 보안을 위해 실제 값은 숨김
            'CPU_THRESHOLD': config.CPU_THRESHOLD,
            'CHECK_INTERVAL': config.CHECK_INTERVAL,
            'THRESHOLD_COUNT': config.THRESHOLD_COUNT,
            'MOUNT_PATH': config.MOUNT_PATH,
            'GET_FOLDER_SIZE_TIMEOUT': config.GET_FOLDER_SIZE_TIMEOUT,
            'SHUTDOWN_WEBHOOK_URL': config.SHUTDOWN_WEBHOOK_URL,
            'SMB_SHARE_NAME': config.SMB_SHARE_NAME,
            'SMB_USERNAME': config.SMB_USERNAME,
            'SMB_PASSWORD': '********',  # 보안을 위해 실제 값은 숨김
            'SMB_COMMENT': config.SMB_COMMENT,
            'SMB_GUEST_OK': config.SMB_GUEST_OK,
            'SMB_READ_ONLY': config.SMB_READ_ONLY,
            'TIMEZONE': config.TIMEZONE
        }
        return jsonify({"status": "success", "config": config_dict})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_config', methods=['POST'])
def update_config():
    """설정 업데이트"""
    try:
        data = request.get_json()
        
        # 필수 환경변수 목록
        env_vars = [
            'PROXMOX_HOST', 'TOKEN_ID', 'SECRET',
            'SHUTDOWN_WEBHOOK_URL', 'SMB_USERNAME', 'SMB_PASSWORD',
            'SMB_SHARE_NAME'
        ]
        
        # 숫자형 변수 목록
        numeric_vars = {
            'CPU_THRESHOLD': float,
            'CHECK_INTERVAL': int,
            'THRESHOLD_COUNT': int,
            'GET_FOLDER_SIZE_TIMEOUT': int
        }
        
        # 환경변수 업데이트
        for key, value in data.items():
            if key in env_vars:
                if key in ['SECRET', 'SMB_PASSWORD'] and value == '********':
                    continue  # 비밀번호가 변경되지 않은 경우 스킵
                
                if not value and key in ['PROXMOX_HOST', 'TOKEN_ID', 'SECRET', 'SHUTDOWN_WEBHOOK_URL', 'SMB_USERNAME', 'SMB_PASSWORD']:
                    return jsonify({
                        "status": "error",
                        "message": f"{key}는 필수 값입니다."
                    }), 400
                
                set_key('.env', key, str(value))
        
        # YAML 설정 업데이트
        yaml_config = {k: v for k, v in data.items() if k not in env_vars}
        Config.update_yaml_config(yaml_config)
        
        return jsonify({
            "status": "success",
            "message": "설정이 업데이트되었습니다. 변경사항을 적용하려면 서비스를 재시작하세요."
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

def run_flask_app():
    """Flask 앱 실행"""
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False) 