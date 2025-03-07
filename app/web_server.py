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
is_landing_only_mode = args.landing_only

def init_server(state, manager, app_config):
    """웹서버 초기화"""
    global current_state, gshare_manager, config, is_setup_complete
    current_state = state
    gshare_manager = manager
    config = app_config
    
    # 설정 파일이 존재하고 필수 환경 변수가 설정되어 있는지 확인
    try:
        with open('config.yaml', 'r') as f:
            yaml_config = yaml.safe_load(f)
        
        # 필수 환경 변수 확인
        required_env_vars = ['PROXMOX_HOST', 'TOKEN_ID', 'SECRET', 'SHUTDOWN_WEBHOOK_URL', 'SMB_USERNAME', 'SMB_PASSWORD']
        for var in required_env_vars:
            if not os.getenv(var):
                is_setup_complete = False
                return
        
        is_setup_complete = True
    except Exception:
        is_setup_complete = False

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
def show_log():
    """로그 페이지 표시"""
    global is_setup_complete
    
    # 설정이 완료되지 않았으면 설정 페이지로 리다이렉트
    if not is_setup_complete:
        return redirect(url_for('setup'))
        
    # 기존 로직은 그대로 유지
    if current_state:
        return render_template('index.html', state=current_state, config=config)
    else:
        return render_template('index.html', state=get_default_state(), config=None)

@app.route('/setup')
def setup():
    """초기 설정 페이지"""
    # 도커 컨테이너의 IP 주소 가져오기
    container_ip = get_container_ip()
    
    # 이미 설정 파일이 있는 경우, 값을 불러와 미리 채움
    config_path = '/config/config.yaml'
    if os.path.exists(config_path):
        try:
            # 기존 설정 파일 로드
            with open(config_path, 'r', encoding='utf-8') as f:
                yaml_config = yaml.safe_load(f)
                
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
                'SMB_GUEST_OK': 'yes' if yaml_config.get('smb', {}).get('guest_ok', False) else 'no',
                'SMB_READ_ONLY': 'yes' if yaml_config.get('smb', {}).get('read_only', True) else 'no',
                'SMB_LINKS_DIR': yaml_config.get('smb', {}).get('links_dir', '/mnt/gshare_links'),
                'TIMEZONE': yaml_config.get('timezone', 'Asia/Seoul')
            }
            
            logging.info("기존 설정 파일에서 값을 불러왔습니다. 랜딩 페이지에 미리 채웁니다.")
            return render_template('landing.html', form_data=form_data, has_config=True, container_ip=container_ip)
            
        except Exception as e:
            logging.error(f"설정 파일 로드 실패: {e}")
    
    # 템플릿 설정 파일에서 초기값 로드
    template_config = Config.load_template_config()
    
    # 폼 초기 데이터 준비
    form_data = {
        'PROXMOX_HOST': template_config['credentials'].get('proxmox_host', ''),
        'TOKEN_ID': template_config['credentials'].get('token_id', ''),
        'SECRET': template_config['credentials'].get('secret', ''),
        'NODE_NAME': template_config['proxmox'].get('node_name', ''),
        'VM_ID': template_config['proxmox'].get('vm_id', ''),
        'CPU_THRESHOLD': template_config['proxmox']['cpu'].get('threshold', 10),
        'CHECK_INTERVAL': template_config['proxmox']['cpu'].get('check_interval', 60),
        'THRESHOLD_COUNT': template_config['proxmox']['cpu'].get('threshold_count', 3),
        'MOUNT_PATH': template_config['mount'].get('path', '/mnt/gshare'),
        'GET_FOLDER_SIZE_TIMEOUT': template_config['mount'].get('folder_size_timeout', 30),
        'NFS_PATH': template_config['nfs'].get('path', ''),
        'SHUTDOWN_WEBHOOK_URL': template_config['credentials'].get('shutdown_webhook_url', ''),
        'SMB_USERNAME': template_config['credentials'].get('smb_username', ''),
        'SMB_PASSWORD': template_config['credentials'].get('smb_password', ''),
        'SMB_SHARE_NAME': template_config['smb'].get('share_name', 'gshare'),
        'SMB_COMMENT': template_config['smb'].get('comment', 'GShare SMB 공유'),
        'SMB_GUEST_OK': 'yes' if template_config['smb'].get('guest_ok', False) else 'no',
        'SMB_READ_ONLY': 'yes' if template_config['smb'].get('read_only', True) else 'no',
        'SMB_LINKS_DIR': template_config['smb'].get('links_dir', '/mnt/gshare_links'),
        'TIMEZONE': template_config.get('timezone', 'Asia/Seoul')
    }
    
    return render_template('landing.html', form_data=form_data, has_config=False, container_ip=container_ip)

@app.route('/save-config', methods=['POST'])
def save_config():
    """설정 저장"""
    global is_setup_complete
    
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
            'TIMEZONE': form_data.get('TIMEZONE', 'Asia/Seoul')
        }
        
        # 설정 파일 업데이트
        Config.update_yaml_config(config_dict)
        
        # 초기화 완료 플래그 생성
        init_flag_path = '/config/.init_complete'
        try:
            with open(init_flag_path, 'w') as f:
                f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        except Exception as e:
            logging.error(f"초기화 완료 플래그 생성 실패: {e}")
        
        # 설정이 완료되었으므로 플래그 설정
        is_setup_complete = True
        
        # 랜딩 페이지 전용 모드에서는 NFS 마운트 및 재시작을 하지 않음
        if not is_landing_only_mode:
            # NFS 마운트 시도
            try:
                mount_path = form_data.get('MOUNT_PATH', '')
                nfs_path = form_data.get('NFS_PATH', '')
                
                # 마운트 디렉토리가 없으면 생성
                if mount_path and nfs_path and not os.path.exists(mount_path):
                    os.makedirs(mount_path, exist_ok=True)
                    
                    # NFS 마운트 시도 (여러 옵션 추가)
                    mount_cmd = f"mount -t nfs -o nolock,vers=3,soft,timeo=100 {nfs_path} {mount_path}"
                    subprocess.run(mount_cmd, shell=True, check=True)
            except Exception as e:
                logging.error(f"NFS 마운트 실패: {e}")
            
            # 앱 재시작을 위한 메시지 전달
            if gshare_manager:
                gshare_manager.restart_required = True
        else:
            # 랜딩 페이지 전용 모드인 경우 설정이 저장되었음을 알리고 컨테이너 재시작 안내
            return render_template('setup_complete.html')
        
        return redirect(url_for('show_log'))
    
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
    if os.path.exists('gshare_manager.log'):
        with open('gshare_manager.log', 'r') as file:
            log_content = file.read()
            return log_content
    else:
        return "Log file not found.", 404

@app.route('/restart_service')
def restart_service():
    try:
        subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager.service'], check=True)
        subprocess.run(['sudo', 'systemctl', 'restart', 'gshare_manager_log_server.service'], check=True)
        return jsonify({"status": "success", "message": "서비스가 재시작되었습니다."})
    except subprocess.CalledProcessError as e:
        return jsonify({"status": "error", "message": f"서비스 재시작 실패: {str(e)}"}), 500

@app.route('/retry_mount')
def retry_mount():
    try:
        # 설정을 다시 로드
        try:
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
    try:
        open('gshare_manager.log', 'w').close()
        return jsonify({"status": "success", "message": "로그가 성공적으로 삭제되었습니다."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"로그 삭제 실패: {str(e)}"}), 500

@app.route('/trim_log/<int:lines>')
def trim_log(lines):
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
            else:
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

def run_landing_page():
    """랜딩 페이지만 실행하는 함수"""
    global is_setup_complete
    is_setup_complete = False
    
    # 환경 변수에서 포트 읽기 (기본값: 5000)
    port = int(os.environ.get('FLASK_PORT', 5000))
    
    app.run(host='0.0.0.0', port=port, debug=False)

def run_flask_app():
    """Flask 앱 실행 함수"""
    # 환경 변수에서 포트 읽기 (기본값: 5000)
    port = int(os.environ.get('FLASK_PORT', 5000))
    
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == "__main__":
    # 랜딩 페이지 전용 모드인 경우
    if args.landing_only:
        logging.info("랜딩 페이지 전용 모드로 실행 중...")
        is_setup_complete = False
        run_flask_app()
    else:
        # 일반 모드에서는 gshare_manager.py에서 호출하는 방식으로 작동
        pass 