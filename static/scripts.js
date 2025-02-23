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

                // 필수 요소들도 존재 여부 확인
                const elements = {
                    lastCheckTimeString: document.querySelector('.last-check-time .time-string'),
                    lastAction: document.querySelector('.last-action'),
                    vmStatus: document.querySelector('.vm-status'),
                    cpuUsage: document.querySelector('.cpu-usage'),
                    lowCpuCount: document.querySelector('.low-cpu-count'),
                    uptime: document.querySelector('.uptime')
                };

                // 각 요소가 존재할 때만 업데이트
                if (elements.lastCheckTimeString) elements.lastCheckTimeString.innerText = data.last_check_time;
                if (elements.lastAction) elements.lastAction.innerText = data.last_action;
                if (elements.vmStatus) {
                    elements.vmStatus.innerText = data.vm_status;
                    updateVMStatus(data.vm_status);
                }
                if (elements.cpuUsage) elements.cpuUsage.innerText = data.cpu_usage + '%';
                if (elements.lowCpuCount) elements.lowCpuCount.innerText = data.low_cpu_count;
                if (elements.uptime) elements.uptime.innerText = data.uptime;

                // 옵셔널한 요소들은 존재 여부 확인 후 업데이트
                const lastFilesChangeTimeReadable = document.querySelector('.last-files-change-time .readable-time');
                const lastFilesChangeTimeString = document.querySelector('.last-files-change-time .time-string');
                const lastShutdownTimeReadable = document.querySelector('.last-shutdown-time .readable-time');
                const lastShutdownTimeString = document.querySelector('.last-shutdown-time .time-string');

                if (lastFilesChangeTimeReadable) {
                    lastFilesChangeTimeReadable.innerText = data.last_size_change_time !== '-' ? 
                        get_time_ago(data.last_size_change_time) : '정보없음';
                }
                if (lastFilesChangeTimeString) {
                    lastFilesChangeTimeString.innerText = data.last_size_change_time;
                }
                if (lastShutdownTimeReadable) {
                    lastShutdownTimeReadable.innerText = data.last_shutdown_time !== '-' ? 
                        get_time_ago(data.last_shutdown_time) : '정보없음';
                }
                if (lastShutdownTimeString) {
                    lastShutdownTimeString.innerText = data.last_shutdown_time;
                }

                // 감시 중인 폴더 목록 업데이트
                const monitoredFoldersContainer = document.querySelector('.monitored-folders-grid');
                if (monitoredFoldersContainer) {
                    const sortedFolders = Object.entries(data.monitored_folders)
                        .sort((a, b) => {
                            const timeA = new Date(a[1].mtime);
                            const timeB = new Date(b[1].mtime);
                            return timeB - timeA; // 최신순 정렬
                        });
                    monitoredFoldersContainer.innerHTML = generateFolderListHtml(sortedFolders);
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

// 폴더 목록 HTML 생성 함수 추가
function generateFolderListHtml(sortedFolders) {
    let foldersHtml = '';
    for (const [folder, info] of sortedFolders) {
        foldersHtml += `
            <div class="flex justify-between items-center bg-white rounded p-2 border ${info.is_mounted ? 'border-green-200' : 'border-red-200'} hover:bg-gray-50 transition-colors duration-200">
                <div class="flex items-center gap-2">
                    <span class="w-2 h-2 rounded-full ${info.is_mounted ? 'bg-green-500' : 'bg-red-500'}"></span>
                    <div class="flex flex-col">
                        <span class="text-sm text-gray-700">${folder}</span>
                        <span class="text-xs text-gray-500 toggle-text">
                            <span class="readable-time">${get_time_ago(info.mtime)}</span>
                            <span class="time-string hidden">${info.mtime}</span>
                        </span>
                    </div>
                </div>
                <button onclick="toggleMount('${folder}')" 
                    class="text-xs px-2 py-1 rounded ${info.is_mounted ? 'bg-red-50 text-red-700 hover:bg-red-100' : 'bg-green-50 text-green-700 hover:bg-green-100'} transition-colors duration-200">
                    ${info.is_mounted ? '마운트 해제' : '마운트'}
                </button>
            </div>
        `;
    }
    return foldersHtml;
}

// toggleMount 함수 내부 수정
function toggleMount(folder) {
    fetch(`/toggle_mount/${encodeURIComponent(folder)}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                // 상태 업데이트를 위해 즉시 새로고침
                fetch('/update_state')
                    .then(response => response.json())
                    .then(data => {
                        const monitoredFoldersContainer = document.querySelector('.monitored-folders-grid');
                        if (monitoredFoldersContainer) {
                            const sortedFolders = Object.entries(data.monitored_folders)
                                .sort((a, b) => {
                                    const timeA = new Date(a[1].mtime);
                                    const timeB = new Date(b[1].mtime);
                                    return timeB - timeA;
                                });
                            monitoredFoldersContainer.innerHTML = generateFolderListHtml(sortedFolders);
                        }
                    });
            } else {
                alert('오류: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('마운트 상태 변경 중 오류가 발생했습니다.');
        });
}