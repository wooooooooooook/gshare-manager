from dataclasses import dataclass, asdict
from typing import Dict, Optional
from datetime import datetime
import pytz

@dataclass
class State:
    last_check_time: str
    vm_running: bool
    cpu_usage: float
    last_action: str
    low_cpu_count: int
    threshold_count: int
    uptime: str
    last_shutdown_time: str
    monitored_folders: dict
    smb_running: bool
    check_interval: int
    
    def to_dict(self):
        data = asdict(self)
        data['vm_status'] = 'ON' if self.vm_running else 'OFF'
        data['smb_status'] = 'ON' if self.smb_running else 'OFF'
        return data

def get_default_state(timezone: str = 'Asia/Seoul') -> State:
    """State가 초기화되지 않았을 때 사용할 기본값을 반환"""
    current_time = datetime.now(tz=pytz.timezone(timezone)).strftime('%Y-%m-%d %H:%M:%S')
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