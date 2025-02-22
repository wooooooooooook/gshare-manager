// get_time_ago 함수
function get_time_ago(timestamp_str) {
    try {
        const last_check = new Date(timestamp_str.replace(' ', 'T') + '+09:00');
        const now = new Date();
        const diff = Math.floor((now - last_check) / 1000);

        let time_ago = "";
        if (diff < 150) {
            time_ago = `${diff}초 전`;
        } else if (diff < 3600) {
            time_ago = `${Math.floor(diff / 60)}분 전`;
        } else if (diff < 86400) {
            time_ago = `${Math.floor(diff / 3600)}시간 전`;
        } else {
            time_ago = `${Math.floor(diff / 86400)}일 전`;
        }

        return time_ago;
    } catch {
        return timestamp_str;
    }
}

// 페재 로그 레벨 가져오기
async function getCurrentLogLevel() {
    try {
        const response = await fetch('/get_log_level');
        const data = await response.json();
        if (data.status === 'success') {
            document.getElementById('logLevel').value = data.current_level;
        }
    } catch (error) {
        console.error('로그 레벨 확인 실패:', error);
    }
}

// 로그 레벨 변경
async function setLogLevel() {
    const level = document.getElementById('logLevel').value;
    try {
        const response = await fetch(`/set_log_level/${level}`);
        const data = await response.json();
        if (data.status === 'success') {
            alert(data.message);
        } else {
            alert('로그 레벨 변경 실패: ' + data.message);
        }
    } catch (error) {
        alert('로그 레벨 변경 중 오류 발생');
        console.error('로그 레벨 변경 실패:', error);
    }
}

// 페이지 로드 시 로그를 맨 아래로 스크롤
window.onload = function () {
    getCurrentLogLevel();
    const logContent = document.getElementById('logContent');
    logContent.scrollTop = logContent.scrollHeight;

    let lastCheckTimeData = '';

    // 초기 VM 상태에 따라 컨테이너 표시 설정
    const initialVMStatus = document.querySelector('.vm-status').innerText;
    updateVMStatus(initialVMStatus);

    // .toggle-text 클릭이벤트 추가
    document.querySelectorAll('.toggle-text').forEach(el => {
        el.addEventListener('click', () => {
            // el 자식요소 중 .time-string, .readable-time 요소 표시/숨김
            el.querySelector('.time-string').classList.toggle('hidden');
            el.querySelector('.readable-time').classList.toggle('hidden');
        });
    });

    // 1초마다 시간 표시 업데이트
    setInterval(function () {
        if (lastCheckTimeData) {
            document.querySelector('.last-check-time .readable-time').innerText = get_time_ago(lastCheckTimeData);
        }
    }, 1000);

    // 5초마다 상태 업데이트 요청
    setInterval(function () {
        fetch('/update_state')
            .then(response => response.json())
            .then(data => {
                lastCheckTimeData = data.last_check_time;
                document.querySelector('.last-check-time .readable-time').innerText = get_time_ago(data.last_check_time);
                document.querySelector('.last-check-time .time-string').innerText = data.last_check_time;
                document.querySelector('.last-action').innerText = data.last_action;
                document.querySelector('.vm-status').innerText = data.vm_status;
                updateVMStatus(data.vm_status);
                document.querySelector('.cpu-usage').innerText = data.cpu_usage + '%';
                document.querySelector('.low-cpu-count').innerText = data.low_cpu_count;
                document.querySelector('.uptime').innerText = data.uptime;
                document.querySelector('.folder-files').innerText = data.folder_size_readable;
                document.querySelector('.last-files-change-time .readable-time').innerText =
                    data.last_size_change_time !== '-' ? get_time_ago(data.last_size_change_time) : '정보없음';
                document.querySelector('.last-files-change-time .time-string').innerText = data.last_size_change_time;
                document.querySelector('.last-shutdown-time .readable-time').innerText =
                    data.last_shutdown_time !== '-' ? get_time_ago(data.last_shutdown_time) : '정보없음';
                document.querySelector('.last-shutdown-time .time-string').innerText = data.last_shutdown_time;

                // 감시 중인 폴더 목록 업데이트
                const monitoredFoldersContainer = document.querySelector('.grid.grid-cols-2.gap-2:last-child');
                if (monitoredFoldersContainer) {
                    let foldersHtml = '';
                    for (const [folder, mtime] of Object.entries(data.monitored_folders)) {
                        foldersHtml += `
                            <div class="flex justify-between items-center bg-white rounded p-2 border border-gray-100">
                                <span class="text-sm text-gray-700">${folder}</span>
                                <span class="text-xs text-gray-500">${get_time_ago(mtime)}</span>
                            </div>
                        `;
                    }
                    monitoredFoldersContainer.innerHTML = foldersHtml;
                }
            });

        // update_log 부분 수정
        fetch('/update_log')
            .then(response => response.text())
            .then(logContent => {
                document.querySelector('#logContent').innerText = logContent;
            });
    }, 5000);
};

function updateVMStatus(status) {
    const vmStatusContainer = document.querySelector('.vm-status-container');
    const vmRunningElements = document.querySelectorAll('.vm-running-element');
    const vmStoppedElements = document.querySelectorAll('.vm-stopped-element');

    if (status === '🟢') {
        // VM 실행 중
        vmStatusContainer.classList.remove('bg-red-50', 'border-red-100');
        vmStatusContainer.classList.add('bg-green-50', 'border-green-100');
        document.querySelector('.vm-status').classList.remove('text-red-800');
        document.querySelector('.vm-status').classList.add('text-green-800');

        // 실행 중 요소들 표시
        vmRunningElements.forEach(el => el.classList.remove('hidden'));
        vmStoppedElements.forEach(el => el.classList.add('hidden'));
    } else {
        // VM 중지됨
        vmStatusContainer.classList.remove('bg-green-50', 'border-green-100');
        vmStatusContainer.classList.add('bg-red-50', 'border-red-100');
        document.querySelector('.vm-status').classList.remove('text-green-800');
        document.querySelector('.vm-status').classList.add('text-red-800');

        // 중지 시 요소들 표시
        vmRunningElements.forEach(el => el.classList.add('hidden'));
        vmStoppedElements.forEach(el => el.classList.remove('hidden'));
    }
} 

function restartService() {
    if (confirm('정말로 서비스를 재시작하시겠습니까?')) {
        // 상태 메시지 표시
        const statusDiv = document.getElementById('restartStatus');
        const statusText = document.getElementById('restartStatusText');
        statusDiv.classList.remove('hidden');
        statusText.textContent = '서비스가 재시작됩니다. 잠시만 기다려주세요...';
        
        fetch('/restart_service')
            .then(async response => {
                if (response.ok) {
                    statusText.textContent = '서비스 재시작이 요청되었습니다. 페이지가 곧 새로고침됩니다.';
                    setTimeout(() => {
                        checkServerAndReload();
                    }, 5000);
                } else {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
            })
            .catch(error => {
                if (error.message.includes('502') || error.message.includes('Failed to fetch')) {
                    statusText.textContent = '서비스가 재시작 중입니다. 페이지가 곧 새로고침됩니다.';
                    setTimeout(() => {
                        checkServerAndReload();
                    }, 5000);
                } else {
                    console.error('Error:', error);
                    statusText.textContent = '서비스 재시작 중 오류가 발생했습니다: ' + error.message;
                    // 3초 후 에러 메시지 숨김
                    setTimeout(() => {
                        statusDiv.classList.add('hidden');
                    }, 3000);
                }
            });
    }
}
function retryMount() {
    if (confirm('마운트를 재시도하시겠습니까?')) {
        // 상태 메시지 표시
        const statusDiv = document.getElementById('restartStatus');
        const statusText = document.getElementById('restartStatusText');
        statusDiv.classList.remove('hidden');
        statusText.textContent = '마운트를 재시도하는 중입니다. 잠시만 기다려주세요...';
        
        fetch('/retry_mount')
            .then(async response => {
                if (response.ok) {
                    statusText.textContent = '마운트 재시도가 요청되었습니다. 페이지가 곧 새로고침됩니다.';
                    setTimeout(() => {
                        checkServerAndReload();
                    }, 5000);
                } else {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
            })
            .catch(error => {
                if (error.message.includes('502') || error.message.includes('Failed to fetch')) {
                    statusText.textContent = '마운트 재시도가 요청되었습니다. 페이지가 곧 새로고침됩니다.';
                    setTimeout(() => {
                        checkServerAndReload();
                    }, 5000);
                } else {
                    console.error('Error:', error);
                    statusText.textContent = '마운트 재시도 중 오류가 발생했습니다: ' + error.message;
                    // 3초 후 에러 메시지 숨김
                    setTimeout(() => {
                        statusDiv.classList.add('hidden');
                    }, 3000);
                }
            });
    }
}

// 서버 상태를 확인하고 페이지를 새로고침하는 함수
function checkServerAndReload() {
    fetch('/update_state')
        .then(response => {
            if (response.ok) {
                location.reload();
            } else {
                // 서버가 아직 준비되지 않았다면 재시도
                setTimeout(checkServerAndReload, 2000);
            }
        })
        .catch(() => {
            // 오류 발생시 재시도
            setTimeout(checkServerAndReload, 2000);
        });
}

function clearLog() {
    if (confirm('정말로 모든 로그를 삭제하시겠습니까?')) {
        fetch('/clear_log')
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    document.getElementById('logContent').innerText = '';
                    alert(data.message);
                } else {
                    alert('오류: ' + data.message);
                }
            })
            .catch(error => {
                console.error('Error:', error);
                alert('로그 삭제 중 오류가 발생했습니다.');
            });
    }
}

function trimLog(lines) {
    if (confirm(`최근 ${lines}줄만 남기고 나머지를 삭제하시겠습니까?`)) {
        fetch(`/trim_log/${lines}`)
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    // 로그 내용 업데이트
                    fetch('/update_log')
                        .then(response => response.text())
                        .then(logContent => {
                            document.getElementById('logContent').innerText = logContent;
                        });
                    alert(data.message);
                } else {
                    alert('오류: ' + data.message);
                }
            })
            .catch(error => {
                console.error('Error:', error);
                alert('로그 정리 중 오류가 발생했습니다.');
            });
    }
}

function startVM() {
    if (confirm('VM을 시작하시겠습니까?')) {
        const statusDiv = document.getElementById('vmControlStatus');
        const statusText = document.getElementById('vmControlStatusText');
        statusDiv.classList.remove('hidden');
        statusText.textContent = 'VM 시작을 요청중입니다...';
        
        fetch('/start_vm')
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    statusText.textContent = data.message;
                    setTimeout(() => {
                        statusDiv.classList.add('hidden');
                    }, 3000);
                } else {
                    throw new Error(data.message);
                }
            })
            .catch(error => {
                statusText.textContent = '오류: ' + error.message;
                setTimeout(() => {
                    statusDiv.classList.add('hidden');
                }, 3000);
            });
    }
}

function shutdownVM() {
    if (confirm('VM을 종료하시겠습니까?')) {
        const statusDiv = document.getElementById('vmControlStatus');
        const statusText = document.getElementById('vmControlStatusText');
        statusDiv.classList.remove('hidden');
        statusText.textContent = 'VM 종료를 요청중입니다...';
        
        fetch('/shutdown_vm')
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    statusText.textContent = data.message;
                    setTimeout(() => {
                        statusDiv.classList.add('hidden');
                    }, 3000);
                } else {
                    throw new Error(data.message);
                }
            })
            .catch(error => {
                statusText.textContent = '오류: ' + error.message;
                setTimeout(() => {
                    statusDiv.classList.add('hidden');
                }, 3000);
            });
    }
}