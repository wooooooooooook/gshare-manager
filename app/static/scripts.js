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

// 로그 레벨 가져오기
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

let checkInterval = 120; // 기본값

// 프로그레스 바 업데이트
function updateProgressBar() {
    const progressBar = document.querySelector('.last-check-progress');
    if (!progressBar) return;

    const now = new Date();
    const lastCheckTime = new Date(document.querySelector('.last-check-time .time-string').innerText.replace(' ', 'T') + '+09:00');
    const elapsedTime = (now - lastCheckTime) / 1000;
    const progress = Math.min(100, (elapsedTime / checkInterval) * 100);
    
    progressBar.style.width = `${progress}%`;
}

// 페이지 로드 시 로그를 맨 아래로 스크롤
window.onload = function () {
    getCurrentLogLevel();
    const logContent = document.getElementById('logContent');
    logContent.scrollTop = logContent.scrollHeight;

    let lastCheckTimeData = '';

    // 초기 VM 상태에 따라 컨테이너 표시 설정
    const initialVMStatus = document.querySelector('.vm-status span').innerText;
    updateVMStatus(initialVMStatus);

    // 초기 SMB 상태에 따라 컨테이너 표시 설정
    const initialSMBStatus = document.querySelector('.smb-status span').innerText;
    updateSMBStatus(initialSMBStatus);

    // .toggle-text 클릭이벤트 추가
    document.querySelectorAll('.toggle-text').forEach(el => {
        el.addEventListener('click', () => {
            el.querySelector('.time-string').classList.toggle('hidden');
            el.querySelector('.readable-time').classList.toggle('hidden');
        });
    });

    // 프로그레스 바 업데이트 시작
    setInterval(updateProgressBar, 1000);  // 1000ms마다 업데이트

    // 1초마다 시간 표시 업데이트
    setInterval(function () {
        // 마지막 체크 시간 업데이트
        const lastCheckTime = document.querySelector('.last-check-time .time-string');
        if (lastCheckTime) {
            const readableTime = document.querySelector('.last-check-time .readable-time');
            readableTime.innerText = get_time_ago(lastCheckTime.innerText);
        }

        // 마지막 종료 시간 업데이트
        const lastShutdownTime = document.querySelector('.last-shutdown-time .time-string');
        if (lastShutdownTime) {
            const readableTime = document.querySelector('.last-shutdown-time .readable-time');
            readableTime.innerText = lastShutdownTime.innerText !== '-' ? 
                get_time_ago(lastShutdownTime.innerText) : '정보없음';
        }

        // 모든 폴더의 수정 시간 업데이트
        document.querySelectorAll('.monitored-folders-grid .toggle-text').forEach(el => {
            const timeString = el.querySelector('.time-string');
            const readableTime = el.querySelector('.readable-time');
            if (timeString && readableTime) {
                readableTime.innerText = get_time_ago(timeString.innerText);
            }
        });
    }, 1000);

    // 1초마다 상태 업데이트 요청
    setInterval(function () {
        fetch('/update_state')
            .then(response => response.json())
            .then(data => {
                // check_interval 업데이트
                if (data.check_interval) {
                    checkInterval = data.check_interval;
                }

                // 필수 요소들 존재 여부 확인 및 업데이트
                const elements = {
                    lastCheckTimeReadable: document.querySelector('.last-check-time .readable-time'),
                    lastCheckTimeString: document.querySelector('.last-check-time .time-string'),
                    lastAction: document.querySelector('.last-action'),
                    vmStatus: document.querySelector('.vm-status'),
                    smbStatus: document.querySelector('.smb-status'),
                    cpuUsage: document.querySelector('.cpu-usage'),
                    lowCpuCount: document.querySelector('.low-cpu-count'),
                    uptime: document.querySelector('.uptime'),
                    lastShutdownTimeReadable: document.querySelector('.last-shutdown-time .readable-time'),
                    lastShutdownTimeString: document.querySelector('.last-shutdown-time .time-string')
                };

                // 각 요소가 존재할 때만 업데이트
                if (elements.lastCheckTimeReadable) elements.lastCheckTimeReadable.innerText = get_time_ago(data.last_check_time);
                if (elements.lastCheckTimeString) elements.lastCheckTimeString.innerText = data.last_check_time;
                if (elements.lastAction) elements.lastAction.innerText = data.last_action;
                if (elements.vmStatus) {
                    const vmStatusSpan = elements.vmStatus.querySelector('span');
                    if (vmStatusSpan) {
                        vmStatusSpan.innerText = data.vm_status;
                        updateVMStatus(data.vm_status);
                    }
                }
                if (elements.smbStatus) {
                    const smbStatusSpan = elements.smbStatus.querySelector('span');
                    if (smbStatusSpan) {
                        smbStatusSpan.innerText = data.smb_status;
                        updateSMBStatus(data.smb_status);
                    }
                }
                if (elements.cpuUsage) elements.cpuUsage.innerText = data.cpu_usage + '%';
                if (elements.lowCpuCount) elements.lowCpuCount.innerText = data.low_cpu_count + '/' + data.threshold_count;
                if (elements.uptime) elements.uptime.innerText = data.uptime;
                if (elements.lastShutdownTimeReadable) {
                    elements.lastShutdownTimeReadable.innerText = data.last_shutdown_time !== '-' ? 
                        get_time_ago(data.last_shutdown_time) : '정보없음';
                }
                if (elements.lastShutdownTimeString) {
                    elements.lastShutdownTimeString.innerText = data.last_shutdown_time;
                }

                // 감시 중인 폴더 목록 업데이트
                const monitoredFoldersContainer = document.querySelector('.monitored-folders-grid');
                if (monitoredFoldersContainer) {
                    const sortedFolders = Object.entries(data.monitored_folders)
                        .sort((a, b) => {
                            // 먼저 마운트 상태로 정렬
                            if (a[1].is_mounted !== b[1].is_mounted) {
                                return b[1].is_mounted ? 1 : -1; // 마운트된 것이 위로
                            }
                            // 마운트 상태가 같다면 수정 시간으로 정렬
                            const timeA = new Date(a[1].mtime);
                            const timeB = new Date(b[1].mtime);
                            return timeB - timeA; // 최신순 정렬
                        });
                    monitoredFoldersContainer.innerHTML = generateFolderListHtml(sortedFolders);

                    // 토글 이벤트 리스너 다시 추가
                    monitoredFoldersContainer.querySelectorAll('.toggle-text').forEach(el => {
                        el.addEventListener('click', () => {
                            el.querySelector('.time-string').classList.toggle('hidden');
                            el.querySelector('.readable-time').classList.toggle('hidden');
                        });
                    });
                }
            });

        // update_log 부분 수정
        fetch('/update_log')
            .then(response => response.text())
            .then(logContent => {
                document.querySelector('#logContent').innerText = logContent;
            });
    }, 1000);
};

function updateVMStatus(status) {
    const vmStatusContainer = document.querySelector('.vm-status-container');
    const vmRunningElements = document.querySelectorAll('.vm-running-element');
    const vmStoppedElements = document.querySelectorAll('.vm-stopped-element');
    const vmStatusSpan = document.querySelector('.vm-status span');

    if (status === 'ON') {
        // VM 실행 중
        vmStatusContainer.classList.remove('bg-red-50', 'border-red-100');
        vmStatusContainer.classList.add('bg-gray-50', 'border-gray-200');
        
        // 상태 표시 스타일 변경
        vmStatusSpan.classList.remove('bg-slate-50', 'text-slate-700');
        vmStatusSpan.classList.add('bg-emerald-50', 'text-emerald-700');

        // 실행 중 요소들 표시
        vmRunningElements.forEach(el => el.classList.remove('hidden'));
        vmStoppedElements.forEach(el => el.classList.add('hidden'));
    } else {
        // VM 중지됨
        vmStatusContainer.classList.remove('bg-green-50', 'border-green-100');
        vmStatusContainer.classList.add('bg-gray-50', 'border-gray-200');
        
        // 상태 표시 스타일 변경
        vmStatusSpan.classList.remove('bg-emerald-50', 'text-emerald-700');
        vmStatusSpan.classList.add('bg-slate-50', 'text-slate-700');

        // 중지 시 요소들 표시
        vmRunningElements.forEach(el => el.classList.add('hidden'));
        vmStoppedElements.forEach(el => el.classList.remove('hidden'));
    }
}

function updateSMBStatus(status) {
    const smbStatusContainer = document.querySelector('.smb-status-container');
    const smbStatusSpan = document.querySelector('.smb-status span');

    if (status === 'ON') {
        // SMB 실행 중
        smbStatusContainer.classList.remove('bg-red-50', 'border-red-100');
        smbStatusContainer.classList.add('bg-gray-50', 'border-gray-200');
        
        // 상태 표시 스타일 변경
        smbStatusSpan.classList.remove('bg-slate-50', 'text-slate-700');
        smbStatusSpan.classList.add('bg-emerald-50', 'text-emerald-700');
    } else {
        // SMB 중지됨
        smbStatusContainer.classList.remove('bg-green-50', 'border-green-100');
        smbStatusContainer.classList.add('bg-gray-50', 'border-gray-200');
        
        // 상태 표시 스타일 변경
        smbStatusSpan.classList.remove('bg-emerald-50', 'text-emerald-700');
        smbStatusSpan.classList.add('bg-slate-50', 'text-slate-700');
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
                    }, 3000);
                } else {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
            })
            .catch(error => {
                if (error.message.includes('502') || error.message.includes('Failed to fetch')) {
                    statusText.textContent = '서비스가 재시작 중입니다. 페이지가 곧 새로고침됩니다.';
                    setTimeout(() => {
                        checkServerAndReload();
                    }, 3000);
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
                    }, 3000);
                } else {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
            })
            .catch(error => {
                if (error.message.includes('502') || error.message.includes('Failed to fetch')) {
                    statusText.textContent = '마운트 재시도가 요청되었습니다. 페이지가 곧 새로고침됩니다.';
                    setTimeout(() => {
                        checkServerAndReload();
                    }, 3000);
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
                        // VM과 SMB 상태 업데이트
                        const vmStatusSpan = document.querySelector('.vm-status span');
                        if (vmStatusSpan) {
                            vmStatusSpan.innerText = data.vm_status;
                            updateVMStatus(data.vm_status);
                        }
                        const smbStatusSpan = document.querySelector('.smb-status span');
                        if (smbStatusSpan) {
                            smbStatusSpan.innerText = data.smb_status;
                            updateSMBStatus(data.smb_status);
                        }

                        // 폴더 목록 업데이트
                        const monitoredFoldersContainer = document.querySelector('.monitored-folders-grid');
                        if (monitoredFoldersContainer) {
                            const sortedFolders = Object.entries(data.monitored_folders)
                                .sort((a, b) => {
                                    // 먼저 마운트 상태로 정렬
                                    if (a[1].is_mounted !== b[1].is_mounted) {
                                        return b[1].is_mounted ? 1 : -1; // 마운트된 것이 위로
                                    }
                                    // 마운트 상태가 같다면 수정 시간으로 정렬
                                    const timeA = new Date(a[1].mtime);
                                    const timeB = new Date(b[1].mtime);
                                    return timeB - timeA; // 최신순 정렬
                                });
                            monitoredFoldersContainer.innerHTML = generateFolderListHtml(sortedFolders);

                            // 토글 이벤트 리스너 다시 추가
                            monitoredFoldersContainer.querySelectorAll('.toggle-text').forEach(el => {
                                el.addEventListener('click', () => {
                                    el.querySelector('.time-string').classList.toggle('hidden');
                                    el.querySelector('.readable-time').classList.toggle('hidden');
                                });
                            });
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