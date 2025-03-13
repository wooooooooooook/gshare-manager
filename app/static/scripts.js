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
// 로그 자동 업데이트 상태 변수 추가
let autoUpdateLog = true;
let logHovered = false;
let autoScrollLog = true; // 자동 스크롤 상태 변수 추가

// state를 콘솔에 로깅하는 함수
function logStateToConsole(state) {
    console.log('=== State 업데이트 ===');
    console.log(`시간: ${new Date().toLocaleString()}`);
    console.log(`VM 상태: ${state.vm_status}`);
    console.log(`SMB 상태: ${state.smb_status}`);
    console.log(`NFS 상태: ${state.nfs_status}`);
    console.log(`CPU 사용률: ${state.cpu_usage}%`);
    console.log(`Low CPU 카운트: ${state.low_cpu_count}/${state.threshold_count}`);
    console.log(`업타임: ${state.uptime}`);
    console.log(`마지막 체크 시간: ${state.last_check_time}`);
    console.log('===================');
}

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

// 로그 자동 업데이트 토글 함수
function toggleLogAutoUpdate() {
    autoUpdateLog = !autoUpdateLog;
    const toggleBtn = document.getElementById('toggleLogUpdate');
    if (toggleBtn) {
        toggleBtn.innerText = autoUpdateLog ? '자동 업데이트 중지' : '자동 업데이트 시작';
        toggleBtn.classList.toggle('bg-gray-50');
        toggleBtn.classList.toggle('bg-yellow-50');
    }
}

// 로그 자동 스크롤 토글 함수
function toggleLogAutoScroll() {
    autoScrollLog = !autoScrollLog;
    const toggleBtn = document.getElementById('toggleLogScroll');
    if (toggleBtn) {
        toggleBtn.innerText = autoScrollLog ? '자동 스크롤 중지' : '자동 스크롤 시작';
        toggleBtn.classList.toggle('bg-gray-50');
        toggleBtn.classList.toggle('bg-yellow-50');
    }
}

// 페이지 로드 시 로그를 맨 아래로 스크롤
window.onload = function () {
    getCurrentLogLevel();
    const logContent = document.getElementById('logContent');
    logContent.scrollTop = logContent.scrollHeight;

    // 로그 영역에 마우스 진입/이탈 이벤트 리스너 추가
    logContent.addEventListener('mouseenter', function() {
        logHovered = true;
    });
    
    logContent.addEventListener('mouseleave', function() {
        logHovered = false;
    });

    // 로그 영역 스크롤 이벤트 감지
    let userScrolled = false;
    let scrollTimeout;
    
    logContent.addEventListener('scroll', function() {
        // 사용자가 맨 아래까지 스크롤했는지 확인
        const isAtBottom = logContent.scrollHeight - logContent.clientHeight <= logContent.scrollTop + 5;
        
        // 맨 아래가 아니면 사용자가 스크롤했다고 표시
        if (!isAtBottom) {
            userScrolled = true;
            clearTimeout(scrollTimeout);
            
            // 5초 후 사용자 스크롤 상태 초기화
            scrollTimeout = setTimeout(() => {
                userScrolled = false;
            }, 5000);
        } else {
            // 맨 아래로 스크롤한 경우 사용자 스크롤 상태 초기화
            userScrolled = false;
            clearTimeout(scrollTimeout);
        }
    });

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

    // 페이지 로드 시 즉시 폴더 상태 업데이트
    updateFolderState();

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
                // state 업데이트를 콘솔에 로깅
                logStateToConsole(data);
                
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

        // update_log 부분 수정 - 마우스가 로그 영역에 있거나 자동 업데이트가 비활성화된 경우 업데이트 중지
        if (autoUpdateLog && !logHovered) {
            fetch('/update_log')
                .then(response => response.text())
                .then(logContent => {
                    const logElement = document.querySelector('#logContent');
                    logElement.innerText = logContent;
                    // 자동 스크롤이 활성화되고 사용자가 직접 스크롤하지 않은 경우에만 맨 아래로 스크롤
                    if (autoScrollLog && !userScrolled) {
                        logElement.scrollTop = logElement.scrollHeight;
                    }
                });
        }
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
                    
                    // VM 시작 후 즉시 state를 가져와서 콘솔에 로깅
                    console.log('=== VM 시작 요청 성공 ===');
                    fetch('/update_state')
                        .then(response => response.json())
                        .then(stateData => {
                            logStateToConsole(stateData);
                        });
                    
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
                    
                    // VM 종료 후 즉시 state를 가져와서 콘솔에 로깅
                    console.log('=== VM 종료 요청 성공 ===');
                    fetch('/update_state')
                        .then(response => response.json())
                        .then(stateData => {
                            logStateToConsole(stateData);
                        });
                    
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
function generateFolderListHtml(sortedFolders, showToggleButtons = true) {
    let foldersHtml = '';
    
    // sortedFolders가 객체인 경우 배열로 변환하고 정렬
    const foldersArray = Array.isArray(sortedFolders) 
        ? sortedFolders 
        : Object.entries(sortedFolders).sort((a, b) => {
            if (a[1].is_mounted !== b[1].is_mounted) {
                return b[1].is_mounted ? 1 : -1;
            }
            return new Date(b[1].mtime) - new Date(a[1].mtime);
        });
    
    for (const entry of foldersArray) {
        const folder = Array.isArray(sortedFolders) ? entry[0] : entry[0];
        const info = Array.isArray(sortedFolders) ? entry[1] : entry[1];
        
        foldersHtml += `
            <div class="flex justify-between items-center p-2 mb-2 border border-gray-200 rounded-lg hover:bg-gray-50">
                <div class="flex-1 overflow-hidden">
                    <div class="text-sm mb-1 font-medium text-gray-800 truncate">${folder}</div>
                    <div class="flex items-center toggle-text text-xs text-gray-500">
                        <svg class="w-3 h-3 mr-1" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
                        </svg>
                        <span class="readable-time">${get_time_ago(info.mtime)}</span>
                        <span class="hidden time-string">${info.mtime}</span>
                    </div>
                </div>
                ${showToggleButtons ? `<button onclick="toggleMount('${folder}')" 
                    class="text-xs px-2 py-1 rounded ${info.is_mounted ? 'bg-red-50 text-red-700 hover:bg-red-100' : 'bg-green-50 text-green-700 hover:bg-green-100'} transition-colors duration-200">
                    ${info.is_mounted ? '마운트 해제' : '마운트'}
                </button>` : ''}
            </div>
        `;
    }
    return foldersHtml;
}

// toggleMount 함수 내부 수정
function toggleMount(folder) {
    // 사용자에게 작업 중임을 시각적으로 표시
    const toggleButtons = document.querySelectorAll(`button[onclick="toggleMount('${folder}')"]`);
    toggleButtons.forEach(btn => {
        btn.disabled = true;
        btn.innerText = '처리 중...';
        btn.classList.add('opacity-50', 'cursor-not-allowed');
    });
    
    // 비동기 요청으로 처리
    fetch(`/toggle_mount/${encodeURIComponent(folder)}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                // 폴더 마운트 상태 변경 후 로깅
                console.log(`=== 폴더 '${folder}' 마운트 상태 변경 성공 ===`);
                fetch('/update_state')
                    .then(response => response.json())
                    .then(stateData => {
                        logStateToConsole(stateData);
                    });
                
                // 별도 함수로 분리하여 상태 업데이트 - 블록 방지
                updateFolderState();
            } else {
                alert('오류: ' + data.message);
                // 버튼 상태 복원
                resetToggleButtons();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('마운트 상태 변경 중 오류가 발생했습니다.');
            // 버튼 상태 복원
            resetToggleButtons();
        });
        
    // 버튼 상태를 복원하는 함수
    function resetToggleButtons() {
        toggleButtons.forEach(btn => {
            btn.disabled = false;
            btn.innerText = document.querySelector(`.monitored-folders-grid [onclick="toggleMount('${folder}')"]`).innerText;
            btn.classList.remove('opacity-50', 'cursor-not-allowed');
        });
    }
}

// 상태 업데이트를 위한 별도 함수
function updateFolderState() {
    // 상태 표시기 추가 - 선택 사항
    const statusIndicator = document.querySelector('.status-update-indicator');
    if (statusIndicator) {
        statusIndicator.classList.remove('hidden');
    }
    
    fetch('/update_state')
        .then(response => response.json())
        .then(data => {
            // VM과 SMB 상태 업데이트 - 중요한 UI 업데이트 먼저 처리
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

            // 백그라운드로 폴더 목록 처리를 위해 requestAnimationFrame 사용
            window.requestAnimationFrame(() => {
                updateFolderList(data.monitored_folders);
            });
        })
        .catch(error => {
            console.error('상태 업데이트 오류:', error);
            if (statusIndicator) {
                statusIndicator.classList.add('hidden');
            }
        });
}

// 폴더 목록만 업데이트하는 함수로 분리
function updateFolderList(folders) {
    // 폴더 목록이 비어있는 경우
    if (!folders || Object.keys(folders).length === 0) {
        document.getElementById('monitoredFoldersContainer').innerHTML = `
            <div class="text-center py-4">
                <p class="text-sm text-gray-600">감시 중인 폴더가 없습니다.</p>
            </div>
        `;
        document.getElementById('smbFoldersContainer').innerHTML = `
            <div class="text-center py-4">
                <p class="text-sm text-gray-600">공유 중인 폴더가 없습니다.</p>
            </div>
        `;
        return;
    }

    // 로딩 요소 숨기기
    const loadingElement = document.getElementById('loadingFolders');
    if (loadingElement) {
        loadingElement.style.display = 'none';
    }
    
    const loadingSmbElement = document.getElementById('loadingSmbFolders');
    if (loadingSmbElement) {
        loadingSmbElement.style.display = 'none';
    }

    // 모니터링 중인 모든 폴더 표시
    const foldersContainer = document.getElementById('monitoredFoldersContainer');
    if (foldersContainer) {
        foldersContainer.innerHTML = generateFolderListHtml(folders);
        
        // 필요한 이벤트 리스너 추가
        foldersContainer.querySelectorAll('.toggle-text').forEach(el => {
            el.addEventListener('click', () => {
                el.querySelector('.time-string').classList.toggle('hidden');
                el.querySelector('.readable-time').classList.toggle('hidden');
            });
        });
    }
    
    // SMB로 공유 중인 폴더만 필터링하여 표시
    const smbFoldersContainer = document.getElementById('smbFoldersContainer');
    if (smbFoldersContainer) {
        // 마운트된 폴더만 필터링
        const mountedFolders = Object.fromEntries(
            Object.entries(folders).filter(([_, info]) => info.is_mounted)
        );
        
        if (Object.keys(mountedFolders).length === 0) {
            smbFoldersContainer.innerHTML = `
                <div class="text-center py-4">
                    <p class="text-sm text-gray-600">현재 SMB로 공유 중인 폴더가 없습니다.</p>
                </div>
            `;
        } else {
            smbFoldersContainer.innerHTML = generateFolderListHtml(mountedFolders, false);
            
            // SMB 폴더 리스트에도 이벤트 리스너 추가
            smbFoldersContainer.querySelectorAll('.toggle-text').forEach(el => {
                el.addEventListener('click', () => {
                    el.querySelector('.time-string').classList.toggle('hidden');
                    el.querySelector('.readable-time').classList.toggle('hidden');
                });
            });
        }
    }
    
    // 상태 표시기 숨기기
    const statusIndicator = document.querySelector('.status-update-indicator');
    if (statusIndicator) {
        statusIndicator.classList.add('hidden');
    }
}