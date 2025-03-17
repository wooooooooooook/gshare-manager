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
let socket = null; // Socket.IO 객체
let userScrolled = false; // userScrolled 변수를 전역 변수로 이동

// state를 콘솔에 로깅하는 함수
function logStateToConsole(state) {
    console.log('=== State 업데이트 ===');
    console.log(state)
    // 마운트된 폴더 갯수 출력
    if (state.monitored_folders) {
        const mountedCount = Object.values(state.monitored_folders).filter(folder => folder.is_mounted).length;
        const totalCount = Object.keys(state.monitored_folders).length;
        console.log(`마운트된 폴더: ${mountedCount}/${totalCount}`);
    } else {
        console.log(`마운트된 폴더: 0/0`);
    }
    
    console.log('===================');
}

// 프로그레스 바 업데이트
function updateProgressBar() {
    const progressBar = document.querySelector('.last-check-progress');
    if (!progressBar) return;
    
    // NFS 상태 확인
    const nfsStatusSpan = document.querySelector('.nfs-status span:first-child');
    if (nfsStatusSpan && nfsStatusSpan.innerText === 'OFF') {
        // NFS가 마운트되지 않은 경우 progress bar를 0%로 설정하고 함수 종료
        progressBar.style.width = '0%';
        return;
    }

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

// Socket.IO 초기화 및 이벤트 핸들러 등록
function initSocketIO() {
    // 기존 소켓이 있으면 연결 해제 및 이벤트 리스너 제거
    if (socket) {
        socket.off('connect');
        socket.off('disconnect');
        socket.off('connect_error');
        socket.off('state_update');
        socket.off('log_update');
        socket.disconnect();
    }

    // Socket.IO 클라이언트 초기화
    socket = io();

    // 소켓 연결 이벤트
    socket.on('connect', function() {
        console.log('Socket.IO 서버에 연결되었습니다.');
        // 연결이 되면 상태 요청
        socket.emit('request_state');
        socket.emit('request_log');
        
        // 폴링이 실행 중이면 중지
        if (pollingInterval) {
            clearInterval(pollingInterval);
            pollingInterval = null;
        }
    });

    // 연결 해제 이벤트
    socket.on('disconnect', function() {
        console.log('Socket.IO 서버와 연결이 끊어졌습니다.');
    });

    // 오류 이벤트
    socket.on('connect_error', function(error) {
        console.error('Socket.IO 연결 오류:', error);
        // 연결 오류 시 폴백으로 HTTP 폴링 사용
        startPolling();
    });

    // 상태 업데이트 이벤트
    socket.on('state_update', function(data) {
        updateUI(data);
    });

    // 로그 업데이트 이벤트
    socket.on('log_update', function(logContent) {
        updateLogContent(logContent);
    });
}

// 상태 UI 업데이트 함수
function updateUI(data) {
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
        nfsStatus: document.querySelector('.nfs-status'),
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
    
    // VM 상태 업데이트
    if (elements.vmStatus) {
        updateVMStatus(data.vm_status);
    }
    
    // SMB 상태 업데이트
    if (elements.smbStatus) {
        updateSMBStatus(data.smb_status);
    }
    
    // NFS 상태 업데이트
    if (elements.nfsStatus) {
        updateNFSStatus(data.nfs_status);
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

    // 감시 중인 폴더 목록 업데이트 - 별도 함수 사용
    if (data.monitored_folders) {
        // 백그라운드로 폴더 목록 처리를 위해 requestAnimationFrame 사용
        window.requestAnimationFrame(() => {
            updateFolderList(data.monitored_folders);
        });
    }
}

// 로그 콘텐츠 업데이트 함수
function updateLogContent(logContent) {
    // 마우스가 로그 영역에 있거나 자동 업데이트가 비활성화된 경우 업데이트 중지
    if (!autoUpdateLog || logHovered) return;
    
    const logElement = document.querySelector('#logContent');
    if (!logElement) return;
    
    logElement.innerText = logContent;
    
    // 자동 스크롤이 활성화되고 사용자가 직접 스크롤하지 않은 경우에만 맨 아래로 스크롤
    if (autoScrollLog && !userScrolled) {
        logElement.scrollTop = logElement.scrollHeight;
    }
}

// HTTP 폴링 시작 함수 (소켓 연결 실패 시 폴백)
let pollingInterval = null; // 인터벌 ID를 저장할 변수 추가

function startPolling() {
    console.warn('Socket.IO 연결 실패, HTTP 폴링으로 전환합니다.');
    
    // 이미 실행 중인 폴링이 있으면 중지
    if (pollingInterval) {
        clearInterval(pollingInterval);
    }
    
    // 1초마다 상태 업데이트 요청
    pollingInterval = setInterval(function() {
        fetch('/update_state')
            .then(response => response.json())
            .then(data => {
                updateUI(data);
            })
            .catch(error => {
                console.error('상태 업데이트 요청 실패:', error);
            });

        // 로그 업데이트 요청
        if (autoUpdateLog && !logHovered) {
            fetch('/update_log')
                .then(response => response.text())
                .then(logContent => {
                    updateLogContent(logContent);
                })
                .catch(error => {
                    console.error('로그 업데이트 요청 실패:', error);
                });
        }
    }, 1000);
}

window.onload = function () {
    getCurrentLogLevel();
    const logContent = document.getElementById('logContent');
    if (logContent) {
        logContent.scrollTop = logContent.scrollHeight;

        // 로그 영역에 마우스 진입/이탈 이벤트 리스너 추가
        logContent.addEventListener('mouseenter', function() {
            logHovered = true;
        });
        
        logContent.addEventListener('mouseleave', function() {
            logHovered = false;
        });

        // 로그 영역 스크롤 이벤트 감지
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
    }

    let lastCheckTimeData = '';

    // 초기 VM 상태에 따라 컨테이너 표시 설정
    const initialVMStatus = document.querySelector('.vm-status span').innerText;
    updateVMStatus(initialVMStatus);

    // 초기 SMB 상태에 따라 컨테이너 표시 설정
    const initialSMBStatus = document.querySelector('.smb-status span').innerText;
    updateSMBStatus(initialSMBStatus);
    
    // 초기 NFS 상태에 따라 컨테이너 표시 설정
    const initialNFSStatus = document.querySelector('.nfs-status span').innerText;
    updateNFSStatus(initialNFSStatus);

    // .toggle-text 클릭이벤트 추가
    document.querySelectorAll('.toggle-text').forEach(el => {
        el.addEventListener('click', () => {
            el.querySelector('.time-string').classList.toggle('hidden');
            el.querySelector('.readable-time').classList.toggle('hidden');
        });
    });

    // 페이지 로드 시 즉시 폴더 상태 업데이트
    updateFolderState();

    // 인터벌 ID를 저장할 변수들
    let progressBarInterval;
    let timeUpdateInterval;

    // 프로그레스 바 업데이트 시작
    progressBarInterval = setInterval(updateProgressBar, 1000);  // 1000ms마다 업데이트

    // 1초마다 시간 표시 업데이트
    timeUpdateInterval = setInterval(function () {
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
        document.querySelectorAll('.monitored-folders-grid .toggle-text, .smb-folders-grid .toggle-text').forEach(el => {
            const timeString = el.querySelector('.time-string');
            const readableTime = el.querySelector('.readable-time');
            if (timeString && readableTime) {
                readableTime.innerText = get_time_ago(timeString.innerText);
            }
        });
    }, 1000);

    // 페이지 언로드 시 인터벌 정리
    window.addEventListener('beforeunload', function() {
        if (progressBarInterval) clearInterval(progressBarInterval);
        if (timeUpdateInterval) clearInterval(timeUpdateInterval);
        if (pollingInterval) clearInterval(pollingInterval);
        
        // 소켓 연결 정리
        if (socket) {
            socket.off('connect');
            socket.off('disconnect');
            socket.off('connect_error');
            socket.off('state_update');
            socket.off('log_update');
            socket.disconnect();
        }
    });

    // Socket.IO 초기화
    try {
        // Socket.IO 스크립트가 로드되었는지 확인
        if (typeof io !== 'undefined') {
            initSocketIO();
        } else {
            // Socket.IO 스크립트가 없으면 폴링으로 폴백
            console.warn('Socket.IO 라이브러리가 로드되지 않았습니다. HTTP 폴링으로 전환합니다.');
            startPolling();
        }
    } catch (error) {
        console.error('Socket.IO 초기화 중 오류:', error);
        startPolling();
    }
};

function updateVMStatus(status) {
    const vmStatusContainer = document.querySelector('.vm-status-container');
    const vmRunningElements = document.querySelectorAll('.vm-running-element');
    const vmStoppedElements = document.querySelectorAll('.vm-stopped-element');
    const vmStatusSpan = document.querySelector('.vm-status span:first-child');
    const statusIcon = document.querySelector('.vm-status span:last-child');

    if (!vmStatusContainer) {
        console.error('VM 상태 컨테이너를 찾을 수 없습니다.');
        return;
    }

    if (status === 'ON') {
        // VM 실행 중
        vmStatusContainer.classList.remove('bg-red-50', 'border-red-100');
        vmStatusContainer.classList.add('bg-gray-50', 'border-gray-200');
        
        // 상태 표시 스타일 변경
        if (vmStatusSpan) {
            vmStatusSpan.classList.remove('bg-slate-50', 'text-slate-700');
            vmStatusSpan.classList.add('bg-emerald-50', 'text-emerald-700');
            vmStatusSpan.innerText = 'ON';
        } else {
            console.warn('VM 상태 표시 요소를 찾을 수 없습니다.');
        }
        
        // 상태 아이콘 변경 (신호등)
        if (statusIcon) {
            statusIcon.classList.remove('bg-red-500');
            statusIcon.classList.add('bg-green-500');
        } else {
            console.warn('VM 상태 아이콘을 찾을 수 없습니다.');
        }

        // 실행 중 요소들 표시
        vmRunningElements.forEach(el => el.classList.remove('hidden'));
        vmStoppedElements.forEach(el => el.classList.add('hidden'));
    } else {
        // VM 중지됨
        vmStatusContainer.classList.remove('bg-green-50', 'border-green-100');
        vmStatusContainer.classList.add('bg-gray-50', 'border-gray-200');
        
        // 상태 표시 스타일 변경
        if (vmStatusSpan) {
            vmStatusSpan.classList.remove('bg-emerald-50', 'text-emerald-700');
            vmStatusSpan.classList.add('bg-slate-50', 'text-slate-700');
            vmStatusSpan.innerText = 'OFF';
        } else {
            console.warn('VM 상태 표시 요소를 찾을 수 없습니다.');
        }
        
        // 상태 아이콘 변경 (신호등)
        if (statusIcon) {
            statusIcon.classList.remove('bg-green-500');
            statusIcon.classList.add('bg-red-500');
        } else {
            console.warn('VM 상태 아이콘을 찾을 수 없습니다.');
        }

        // 중지 시 요소들 표시
        vmRunningElements.forEach(el => el.classList.add('hidden'));
        vmStoppedElements.forEach(el => el.classList.remove('hidden'));
    }
}

function updateSMBStatus(status) {
    const smbStatusContainer = document.querySelector('.smb-status-container');
    const smbStatusSpan = document.querySelector('.smb-status span:first-child');
    const statusIcon = document.querySelector('.smb-status span:last-child');

    if (!smbStatusContainer) {
        console.error('SMB 상태 컨테이너를 찾을 수 없습니다.');
        return;
    }

    if (status === 'ON') {
        // SMB 실행 중
        smbStatusContainer.classList.remove('bg-red-50', 'border-red-100');
        smbStatusContainer.classList.add('bg-gray-50', 'border-gray-200');
        
        // 상태 표시 스타일 변경
        if (smbStatusSpan) {
            smbStatusSpan.classList.remove('bg-slate-50', 'text-slate-700');
            smbStatusSpan.classList.add('bg-emerald-50', 'text-emerald-700');
            smbStatusSpan.innerText = 'ON';
        } else {
            console.warn('SMB 상태 표시 요소를 찾을 수 없습니다.');
        }
        
        // 상태 아이콘 변경 (신호등)
        if (statusIcon) {
            statusIcon.classList.remove('bg-red-500');
            statusIcon.classList.add('bg-green-500');
        } else {
            console.warn('SMB 상태 아이콘을 찾을 수 없습니다.');
        }
    } else {
        // SMB 중지됨
        smbStatusContainer.classList.remove('bg-green-50', 'border-green-100');
        smbStatusContainer.classList.add('bg-gray-50', 'border-gray-200');
        
        // 상태 표시 스타일 변경
        if (smbStatusSpan) {
            smbStatusSpan.classList.remove('bg-emerald-50', 'text-emerald-700');
            smbStatusSpan.classList.add('bg-slate-50', 'text-slate-700');
            smbStatusSpan.innerText = 'OFF';
        } else {
            console.warn('SMB 상태 표시 요소를 찾을 수 없습니다.');
        }
        
        // 상태 아이콘 변경 (신호등)
        if (statusIcon) {
            statusIcon.classList.remove('bg-green-500');
            statusIcon.classList.add('bg-red-500');
        } else {
            console.warn('SMB 상태 아이콘을 찾을 수 없습니다.');
        }
    }
}

function updateNFSStatus(status) {
    const nfsStatusContainer = document.querySelector('.nfs-status-container');
    const nfsStatusSpan = document.querySelector('.nfs-status span:first-child');
    const statusIcon = document.querySelector('.nfs-status span:last-child');
    const smbPanel = document.querySelector('.smb-status-container').closest('.flex.flex-col');
    const nfsWarning = document.querySelector('.bg-red-50.text-red-700.p-2.rounded-lg.mb-2.border.border-red-200');

    if (!nfsStatusContainer) {
        console.error('NFS 상태 컨테이너를 찾을 수 없습니다.');
        return;
    }

    if (status === 'ON') {
        // NFS 마운트됨
        nfsStatusContainer.classList.remove('bg-red-50', 'border-red-100');
        nfsStatusContainer.classList.add('bg-gray-50', 'border-gray-200');
        
        // 상태 표시 스타일 변경
        if (nfsStatusSpan) {
            nfsStatusSpan.classList.remove('bg-slate-50', 'text-slate-700');
            nfsStatusSpan.classList.add('bg-emerald-50', 'text-emerald-700');
            nfsStatusSpan.innerText = 'ON';
        } else {
            console.warn('NFS 상태 표시 요소를 찾을 수 없습니다.');
        }
        
        // 상태 아이콘 변경 (신호등)
        if (statusIcon) {
            statusIcon.classList.remove('bg-red-500');
            statusIcon.classList.add('bg-green-500');
        } else {
            console.warn('NFS 상태 아이콘을 찾을 수 없습니다.');
        }
        
        // NFS 경고 메시지 숨기기
        if (nfsWarning) {
            nfsWarning.classList.add('hidden');
        }
        
        // SMB 패널 활성화
        if (smbPanel) {
            smbPanel.classList.remove('opacity-50', 'pointer-events-none');
        }
        
        // 프로그레스 바 갱신 시작
        updateProgressBar();
    } else {
        // NFS 마운트 해제됨
        nfsStatusContainer.classList.remove('bg-green-50', 'border-green-100');
        nfsStatusContainer.classList.add('bg-gray-50', 'border-gray-200');
        
        // 상태 표시 스타일 변경
        if (nfsStatusSpan) {
            nfsStatusSpan.classList.remove('bg-emerald-50', 'text-emerald-700');
            nfsStatusSpan.classList.add('bg-slate-50', 'text-slate-700');
            nfsStatusSpan.innerText = 'OFF';
        } else {
            console.warn('NFS 상태 표시 요소를 찾을 수 없습니다.');
        }
        
        // 상태 아이콘 변경 (신호등)
        if (statusIcon) {
            statusIcon.classList.remove('bg-green-500');
            statusIcon.classList.add('bg-red-500');
        } else {
            console.warn('NFS 상태 아이콘을 찾을 수 없습니다.');
        }
        
        // NFS 경고 메시지 표시
        if (nfsWarning) {
            nfsWarning.classList.remove('hidden');
        }
        
        // SMB 패널 비활성화
        if (smbPanel) {
            smbPanel.classList.add('opacity-50', 'pointer-events-none');
        }
        
        // 프로그레스 바 초기화
        const progressBar = document.querySelector('.last-check-progress');
        if (progressBar) {
            progressBar.style.width = '0%';
        }
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
                    
                    // VM 시작 후 서버 상태 변경을 위한 충분한 지연 시간 추가 (2초)
                    setTimeout(() => {
                        // 상태를 업데이트하여 UI 반영
                        updateFolderState();
                        
                        statusDiv.classList.add('hidden');
                    }, 2000);
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
                    
                    // VM 종료 후 서버 상태 변경을 위한 충분한 지연 시간 추가 (2초)
                    setTimeout(() => {
                        // 상태를 업데이트하여 UI 반영
                        updateFolderState();
                        
                        statusDiv.classList.add('hidden');
                    }, 2000);
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
function generateFolderListHtml(sortedFolders, showToggleButtons = true, action = 'mount') {
    
    let foldersHtml = '';
    
    // sortedFolders가 비어있거나 정의되지 않은 경우 처리
    if (!sortedFolders || (typeof sortedFolders === 'object' && Object.keys(sortedFolders).length === 0)) {
        return '<div class="text-center py-4"><p class="text-sm text-gray-600">폴더가 없습니다.</p></div>';
    }
    
    try {
        // sortedFolders가 객체인 경우 배열로 변환하고 정렬
        const foldersArray = Array.isArray(sortedFolders) 
            ? sortedFolders 
            : Object.entries(sortedFolders).sort((a, b) => {
                // 시간순 정렬
                return new Date(b[1].mtime) - new Date(a[1].mtime);
            });
                
        if (!foldersArray.length) {
            return '<div class="text-center py-4"><p class="text-sm text-gray-600">폴더가 없습니다.</p></div>';
        }
        
        for (const entry of foldersArray) {
            const folder = Array.isArray(sortedFolders) ? entry[0] : entry[0];
            const info = Array.isArray(sortedFolders) ? entry[1] : entry[1];
            
            // 폴더 경로에서 안전한 ID 생성 (한글 등 Latin1 범위 밖의 문자 처리)
            const folderId = `folder-${encodeURIComponent(folder).replace(/%/g, '_')}`;
            
            // 버튼 텍스트와 스타일 결정
            let buttonText, buttonClass;
            if (action === 'mount') {
                buttonText = '마운트';
                buttonClass = 'bg-green-50 text-green-700 hover:bg-green-100';
            } else {
                buttonText = '마운트 해제';
                buttonClass = 'bg-red-50 text-red-700 hover:bg-red-100';
            }
            
            foldersHtml += `
                <div id="${folderId}" class="folder-item flex justify-between items-center p-2 mb-2 border border-gray-200 rounded-lg hover:bg-gray-50" data-folder="${folder}" data-mtime="${info.mtime}" data-action="${action}">
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
                    ${showToggleButtons ? `<button onclick="toggleMount('${folder}', '${action}')" 
                        class="toggle-btn text-xs px-2 py-1 rounded ${buttonClass} transition-colors duration-200">
                        ${buttonText}
                    </button>` : ''}
                </div>
            `;
        }
        
        return foldersHtml;
    } catch (error) {
        console.error('generateFolderListHtml 오류:', error);
        return '<div class="text-center py-4"><p class="text-sm text-gray-600">폴더 목록 생성 중 오류가 발생했습니다.</p></div>';
    }
}

// toggleMount 함수 내부 수정
function toggleMount(folder, action = 'mount') {
    // 사용자에게 작업 중임을 시각적으로 표시
    const toggleButtons = document.querySelectorAll(`button[onclick="toggleMount('${folder}', '${action}')"]`);
    toggleButtons.forEach(btn => {
        btn.disabled = true;
        btn.innerText = '처리 중...';
        btn.classList.add('opacity-50', 'cursor-not-allowed');
    });
    
    console.log(`폴더 ${folder}에 대한 ${action} 작업 요청`);
    
    // 비동기 요청으로 처리
    fetch(`/toggle_mount/${encodeURIComponent(folder)}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                // 폴더 마운트 상태 변경 후 로깅
                console.log(`=== 폴더 '${folder}' 마운트 상태 변경 성공 ===`);
                
                // 서버 상태 변경을 위한 충분한 지연 시간 추가 (1.5초)
                setTimeout(() => {
                    // 상태 업데이트 요청
                    updateFolderState();
                    
                    // 버튼 상태 복원
                    resetToggleButtons();
                }, 1500);
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
            // action에 따라 복원할 텍스트 결정
            const buttonText = action === 'mount' ? '마운트' : '마운트 해제';
            btn.innerText = buttonText;
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
            // VM 상태 업데이트 - 선택자 수정
            const vmStatusSpan = document.querySelector('.vm-status span:first-child');
            if (vmStatusSpan) {
                updateVMStatus(data.vm_status);
            }
            
            // SMB 상태 업데이트 - 선택자 수정
            const smbStatusSpan = document.querySelector('.smb-status span:first-child');
            if (smbStatusSpan) {
                updateSMBStatus(data.smb_status);
            }
            
            // NFS 상태 업데이트 - 선택자 수정
            const nfsStatusSpan = document.querySelector('.nfs-status span:first-child');
            if (nfsStatusSpan) {
                updateNFSStatus(data.nfs_status);
            }

            // 백그라운드로 폴더 목록 처리
            if (data.monitored_folders && Object.keys(data.monitored_folders).length > 0) {
                window.requestAnimationFrame(() => {
                    updateFolderList(data.monitored_folders);
                });
            }
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

    // 마운트된 폴더와 마운트되지 않은 폴더 분류
    const unmountedFolders = {};
    const mountedFolders = {};
    
    // 각 폴더를 마운트 상태에 따라 분류
    Object.entries(folders).forEach(([path, info]) => {
        if (info.is_mounted) {
            mountedFolders[path] = info;
        } else {
            unmountedFolders[path] = info;
        }
    });
    
    console.log('마운트되지 않은 폴더 갯수:', Object.keys(unmountedFolders).length);
    console.log('마운트된 폴더 갯수:', Object.keys(mountedFolders).length);

    // 비동기적으로 컨테이너 업데이트 (타이밍 조정)
    setTimeout(() => {
        // NFS 패널에는 마운트되지 않은 폴더만 표시 (마운트 가능한 목록)
        updateFolderContainer('monitoredFoldersContainer', unmountedFolders, 'mount');
    }, 0);
    
    setTimeout(() => {
        // SMB 패널에는 마운트된 폴더만 표시 (언마운트 가능한 목록)
        updateFolderContainer('smbFoldersContainer', mountedFolders, 'unmount');
        
        // 상태 표시기 숨기기
        const statusIndicator = document.querySelector('.status-update-indicator');
        if (statusIndicator) {
            statusIndicator.classList.add('hidden');
        }
    }, 10);
}

// 폴더 컨테이너 업데이트 함수
function updateFolderContainer(containerId, folderData, action) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    // 폴더가 없는 경우 메시지 표시
    if (!folderData || Object.keys(folderData).length === 0) {
        const emptyMessage = action === 'mount' 
            ? '모든 폴더가 마운트되었습니다.' 
            : '현재 SMB로 공유 중인 폴더가 없습니다.';
            
        container.innerHTML = `
            <div class="text-center py-4">
                <p class="text-sm text-gray-600">${emptyMessage}</p>
            </div>
        `;
        return;
    }
    
    // 폴더 데이터를 시간순으로 정렬 (미리 한 번만 수행)
    const sortedFolders = Object.entries(folderData).sort((a, b) => {
        return new Date(b[1].mtime) - new Date(a[1].mtime);
    });
    
    // 현재 컨테이너에 있는 모든 폴더 항목 가져오기
    const existingFolderItems = container.querySelectorAll('.folder-item');
    const existingFolderMap = new Map();
    
    // 기존 폴더 항목을 Map에 저장
    existingFolderItems.forEach(item => {
        existingFolderMap.set(item.dataset.folder, item);
    });
    
    // DOM 조작을 최소화하기 위한 DocumentFragment 사용
    const fragment = document.createDocumentFragment();
    
    // 한 번에 처리할 폴더 수 (청크 크기)
    const CHUNK_SIZE = 20;  
    
    // 비동기 처리를 위한 청크 처리 함수
    function processChunk(startIndex) {
        return new Promise(resolve => {
            // requestAnimationFrame을 사용하여 UI 스레드 최적화
            requestAnimationFrame(() => {
                const endIndex = Math.min(startIndex + CHUNK_SIZE, sortedFolders.length);
                
                // 현재 청크의 폴더 항목 처리
                for (let i = startIndex; i < endIndex; i++) {
                    const [folder, info] = sortedFolders[i];
                    
                    // 폴더 경로에서 안전한 ID 생성 (한글 등 Latin1 범위 밖의 문자 처리)
                    const folderId = `folder-${encodeURIComponent(folder).replace(/%/g, '_')}`;
                    
                    // 이미 존재하는 폴더 항목인지 확인
                    if (existingFolderMap.has(folder)) {
                        // 기존 항목 업데이트
                        const existingItem = existingFolderMap.get(folder);
                        
                        // 수정 시간 업데이트
                        const timeString = existingItem.querySelector('.time-string');
                        const readableTime = existingItem.querySelector('.readable-time');
                        
                        if (timeString && readableTime && timeString.innerText !== info.mtime) {
                            timeString.innerText = info.mtime;
                            readableTime.innerText = get_time_ago(info.mtime);
                            existingItem.dataset.mtime = info.mtime;
                        }
                        
                        // 프래그먼트에 추가
                        fragment.appendChild(existingItem);
                        
                        // 처리된 항목은 Map에서 제거
                        existingFolderMap.delete(folder);
                    } else {
                        // 새 항목 생성
                        const newItem = document.createElement('div');
                        newItem.id = folderId;
                        newItem.className = 'folder-item flex justify-between items-center p-2 mb-2 border border-gray-200 rounded-lg hover:bg-gray-50';
                        newItem.dataset.folder = folder;
                        newItem.dataset.mtime = info.mtime;
                        newItem.dataset.action = action;
                        
                        // 버튼 텍스트와 스타일 결정
                        let buttonText, buttonClass;
                        if (action === 'mount') {
                            buttonText = '마운트';
                            buttonClass = 'bg-green-50 text-green-700 hover:bg-green-100';
                        } else {
                            buttonText = '마운트 해제';
                            buttonClass = 'bg-red-50 text-red-700 hover:bg-red-100';
                        }
                        
                        newItem.innerHTML = `
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
                            <button onclick="toggleMount('${folder}', '${action}')" 
                                class="toggle-btn text-xs px-2 py-1 rounded ${buttonClass} transition-colors duration-200">
                                ${buttonText}
                            </button>
                        `;
                        
                        // 프래그먼트에 추가
                        fragment.appendChild(newItem);
                    }
                }
                
                // 다음 청크 처리 또는 완료
                if (endIndex < sortedFolders.length) {
                    // 다음 청크를 처리할 때 시간을 주어 UI 반응성 유지
                    setTimeout(() => {
                        resolve(processChunk(endIndex));
                    }, 0);
                } else {
                    // 남은 항목들은 제거 대상
                    existingFolderMap.forEach(item => {
                        item.remove();
                    });
                    
                    // 컨테이너 비우기
                    container.innerHTML = '';
                    
                    // 모든 항목을 한 번에 추가 (DOM 조작 최소화)
                    container.appendChild(fragment);
                    
                    // 토글 텍스트 클릭 이벤트 리스너 추가
                    container.querySelectorAll('.toggle-text').forEach(el => {
                        el.addEventListener('click', () => {
                            el.querySelector('.time-string').classList.toggle('hidden');
                            el.querySelector('.readable-time').classList.toggle('hidden');
                        });
                    });
                    
                    resolve();
                }
            });
        });
    }
    
    // 처리 시작
    processChunk(0).catch(error => {
        console.error('폴더 목록 처리 중 오류:', error);
    });
}

// SMB 서비스 토글 함수 추가
function toggleSMB() {
    // 현재 SMB 상태 확인
    const smbStatusSpan = document.querySelector('.smb-status span:first-child');
    if (!smbStatusSpan) {
        console.error('SMB 상태 요소를 찾을 수 없습니다.');
        return;
    }
    
    const currentStatus = smbStatusSpan.innerText;
    console.log(`현재 SMB 상태: ${currentStatus}`);
    
    // 상태에 따라 다른 메시지 표시
    const message = currentStatus === 'ON' 
        ? 'SMB 서비스를 끄시겠습니까? 공유된 폴더에 접근할 수 없게 됩니다.' 
        : 'SMB 서비스를 켜시겠습니까? 마운트된 폴더가 네트워크에 공유됩니다.';
    
    // 확인 대화상자 표시
    if (confirm(message)) {
        // 상태 표시기 표시
        const smbStatusDiv = document.querySelector('.smb-status');
        
        // 클릭 불가능하게 만들기
        if (smbStatusDiv) {
            smbStatusDiv.style.pointerEvents = 'none';
            smbStatusDiv.style.opacity = '0.5';
        }
        
        console.log('SMB 토글 요청 시작');
        
        // 현재 상태에 따라 다른 엔드포인트 호출
        const endpoint = currentStatus === 'ON' ? '/deactivate_smb' : '/activate_smb';
        
        // 서버에 SMB 토글 요청 보내기
        fetch(endpoint)
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    console.log(`SMB ${currentStatus === 'ON' ? '비활성화' : '활성화'} 성공: ${data.message}`);
                    
                    // 서버 상태 변경을 위한 충분한 지연 시간 추가 (2초)
                    setTimeout(() => {
                        // 상태를 업데이트하여 UI 반영
                        updateFolderState();
                        
                        // 다시 클릭 가능하게 만들기
                        if (smbStatusDiv) {
                            smbStatusDiv.style.pointerEvents = 'auto';
                            smbStatusDiv.style.opacity = '1';
                        }
                    }, 2000);
                } else {
                    alert('오류: ' + data.message);
                    
                    // 다시 클릭 가능하게 만들기
                    if (smbStatusDiv) {
                        smbStatusDiv.style.pointerEvents = 'auto';
                        smbStatusDiv.style.opacity = '1';
                    }
                }
            })
            .catch(error => {
                console.error('SMB 토글 중 오류:', error);
                alert('SMB 토글 중 오류가 발생했습니다.');
                
                // 다시 클릭 가능하게 만들기
                if (smbStatusDiv) {
                    smbStatusDiv.style.pointerEvents = 'auto';
                    smbStatusDiv.style.opacity = '1';
                }
            });
    }
}

// VM 토글 함수 추가
function toggleVM() {
    // 현재 VM 상태 확인
    const vmStatusSpan = document.querySelector('.vm-status span:first-child');
    if (!vmStatusSpan) {
        console.error('VM 상태 요소를 찾을 수 없습니다.');
        return;
    }
    
    const currentStatus = vmStatusSpan.innerText;
    console.log(`현재 VM 상태: ${currentStatus}`);
    
    // 상태에 따라 시작 또는 종료 함수 호출
    if (currentStatus === 'OFF') {
        startVM();
    } else {
        shutdownVM();
    }
}

// NFS 서비스 토글 함수 추가
function remountNFS() {
    // 확인 대화상자 표시
    if (confirm('NFS 연결을 다시 시도하시겠습니까? 현재 연결에 문제가 있는 경우 사용하세요.')) {
        // 상태 표시기 표시
        const nfsStatusDiv = document.querySelector('.nfs-status');
        
        // 클릭 불가능하게 만들기
        if (nfsStatusDiv) {
            nfsStatusDiv.style.pointerEvents = 'none';
            nfsStatusDiv.style.opacity = '0.5';
        }
        
        console.log('NFS remount 요청 시작');
        
        // 서버에 NFS remount 요청 보내기
        fetch('/remount_nfs')
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    console.log(`NFS remount 성공: ${data.message}`);
                    
                    // 서버 상태 변경을 위한 충분한 지연 시간 추가 (2초)
                    setTimeout(() => {
                        // 상태를 업데이트하여 UI 반영
                        updateFolderState();
                        
                        // 다시 클릭 가능하게 만들기
                        if (nfsStatusDiv) {
                            nfsStatusDiv.style.pointerEvents = 'auto';
                            nfsStatusDiv.style.opacity = '1';
                        }
                    }, 2000);
                } else {
                    alert('오류: ' + data.message);
                    
                    // 다시 클릭 가능하게 만들기
                    if (nfsStatusDiv) {
                        nfsStatusDiv.style.pointerEvents = 'auto';
                        nfsStatusDiv.style.opacity = '1';
                    }
                }
            })
            .catch(error => {
                console.error('NFS remount 중 오류:', error);
                alert('NFS remount 중 오류가 발생했습니다.');
                
                // 다시 클릭 가능하게 만들기
                if (nfsStatusDiv) {
                    nfsStatusDiv.style.pointerEvents = 'auto';
                    nfsStatusDiv.style.opacity = '1';
                }
            });
    }
}