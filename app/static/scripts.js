// 날짜 파싱 헬퍼 함수
function parseDate(dateStr) {
    if (!dateStr || dateStr === '-') return null;
    try {
        // 이미 ISO 형식이면 (T와 타임존 포함) 그대로 파싱
        if (dateStr.includes('T') && (dateStr.includes('+') || dateStr.endsWith('Z'))) {
            return new Date(dateStr);
        }
        // 아니면 "YYYY-MM-DD HH:MM:SS" 형식으로 가정하고 KST(+09:00) 추가
        return new Date(dateStr.replace(' ', 'T') + '+09:00');
    } catch (e) {
        console.error("Date parsing error:", e);
        return null;
    }
}

function toSortableTimestamp(dateStr) {
    const parsed = parseDate(dateStr);
    if (!parsed || isNaN(parsed.getTime())) {
        return Number.NEGATIVE_INFINITY;
    }
    return parsed.getTime();
}

// get_time_ago 함수
function get_time_ago(timestamp_str) {
    try {
        const last_check = parseDate(timestamp_str);

        // 파싱 실패 시 원본 반환
        if (!last_check || isNaN(last_check.getTime())) return timestamp_str;

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

let checkInterval = 60; // 기본값
// 로그 자동 업데이트 상태 변수 추가
let autoUpdateLog = true;
let logHovered = false;
let autoScrollLog = true; // 자동 스크롤 상태 변수 추가
let socket = null; // Socket.IO 객체
let userScrolled = false; // userScrolled 변수를 전역 변수로 이동
let monitorMode = 'event';
let initialScanInProgress = false;
let hasReceivedStateUpdate = false;

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
    const timeString = document.querySelector('.last-check-time .time-string').innerText;
    const lastCheckTime = parseDate(timeString);

    // 파싱 실패 시 함수 종료
    if (!lastCheckTime || isNaN(lastCheckTime.getTime())) return;

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
    socket.on('connect', function () {
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
    socket.on('disconnect', function () {
        console.log('Socket.IO 서버와 연결이 끊어졌습니다.');
    });

    // 오류 이벤트
    socket.on('connect_error', function (error) {
        console.error('Socket.IO 연결 오류:', error);
        // 연결 오류 시 폴백으로 HTTP 폴링 사용
        startPolling();
    });

    // 상태 업데이트 이벤트
    socket.on('state_update', function (data) {
        updateUI(data);
    });

    // 로그 업데이트 이벤트
    socket.on('log_update', function (logContent) {
        updateLogContent(logContent);
    });

    // 트랜스코딩 진행 상황 이벤트
    socket.on('transcoding_progress', function (data) {
        handleTranscodingProgress(data);
    });
}

// 상태 UI 업데이트 함수
function updateUI(data) {
    // 기능 토글 상태 업데이트
    const toggles = {
        'gshare': data.gshare_enabled,
        'mqtt': data.mqtt_enabled,
        'nfs-mount': data.nfs_mount_enabled,
        'polling': data.polling_enabled,
        'event': data.event_enabled,
        'smb': data.smb_enabled,
        'vm-monitor': data.vm_monitor_enabled
    };

    for (const [key, value] of Object.entries(toggles)) {
        const checkbox = document.getElementById(`toggle-${key}`);
        if (checkbox) {
            // 사용자가 조작 중이 아닐 때만 업데이트 (disabled 상태가 아닐 때)
            if (!checkbox.disabled) {
                // undefined 체크
                if (value !== undefined) {
                    checkbox.checked = value;
                }
            }
        }
    }

    // state 업데이트를 콘솔에 로깅
    logStateToConsole(data);

    // check_interval 업데이트
    if (Number.isFinite(Number(data.check_interval))) {
        checkInterval = Number(data.check_interval);
    }

    const checkIntervalText = document.getElementById('checkIntervalText');
    if (checkIntervalText) {
        checkIntervalText.innerText = checkInterval;
    }

    if (data.monitor_mode) {
        monitorMode = data.monitor_mode;
    }

    // 총 감시 폴더 개수 표시
    const totalFolderBadge = document.getElementById('totalFolderCountbadge');
    if (totalFolderBadge && data.monitored_folders) {
        const totalCount = Object.keys(data.monitored_folders).length;
        if (totalCount > 0) {
            totalFolderBadge.innerText = `${totalCount}개 감시중`;
            totalFolderBadge.classList.remove('hidden');
        } else {
            totalFolderBadge.classList.add('hidden');
        }
    }

    hasReceivedStateUpdate = true;
    initialScanInProgress = Boolean(data.initial_scan_in_progress);

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
        lastShutdownTimeString: document.querySelector('.last-shutdown-time .time-string'),
        relayLastSeenReadable: document.querySelector('.relay-last-seen-readable'),
        relayLastSeenString: document.querySelector('.relay-last-seen-string')
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
    updateInitialScanNotice(initialScanInProgress);
    updateRelayStatus(data.relay_status, data.relay_last_seen);

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
    if (data.monitored_folders && Object.keys(data.monitored_folders).length > 0) {
        // mtime 기준으로 정렬된 폴더 배열 생성
        const sortedFolders = Object.entries(data.monitored_folders).sort((a, b) => {
            return toSortableTimestamp(b[1].mtime) - toSortableTimestamp(a[1].mtime);
        });

        // 백그라운드로 폴더 목록 처리를 위해 requestAnimationFrame 사용
        window.requestAnimationFrame(() => {
            updateFolderList(sortedFolders);
        });
    } else if (data.monitored_folders) {
        // 폴더가 없는 경우 빈 배열 전달
        window.requestAnimationFrame(() => {
            updateFolderList([]);
        });
    }
}

function updateInitialScanNotice(inProgress) {
    const notice = document.getElementById('nfsScanNotice');
    const nfsWarning = document.getElementById('nfsWarning');
    if (!notice) return;

    if (inProgress) {
        notice.classList.remove('hidden');
        if (nfsWarning) nfsWarning.classList.add('hidden');
        return;
    }

    notice.classList.add('hidden');
}


function updateRelayStatus(relayStatus, relayLastSeen) {
    const relayContainer = document.getElementById('relayStatusContainer');
    const relayLastSeenContainer = document.getElementById('relayLastSeen');
    const relayBadge = document.getElementById('relayStatusBadge');
    const relayDot = document.getElementById('relayStatusDot');
    const relayReadable = document.querySelector('.relay-last-seen-readable');
    const relayRaw = document.querySelector('.relay-last-seen-string');

    if (!relayContainer || !relayLastSeenContainer || !relayBadge || !relayDot) {
        return;
    }

    if (monitorMode !== 'event') {
        relayContainer.classList.add('hidden');
        relayLastSeenContainer.classList.add('hidden');
        return;
    }

    relayContainer.classList.remove('hidden');
    relayLastSeenContainer.classList.remove('hidden');

    const normalizedStatus = relayStatus || 'UNKNOWN';
    relayBadge.innerText = normalizedStatus;

    relayBadge.classList.remove('bg-emerald-50', 'text-emerald-700', 'bg-red-50', 'text-red-700', 'bg-slate-50', 'text-slate-700');
    relayDot.classList.remove('bg-green-500', 'bg-red-500', 'bg-gray-400');

    if (normalizedStatus === 'ON') {
        relayBadge.classList.add('bg-emerald-50', 'text-emerald-700');
        relayDot.classList.add('bg-green-500');
    } else if (normalizedStatus === 'OFF') {
        relayBadge.classList.add('bg-red-50', 'text-red-700');
        relayDot.classList.add('bg-red-500');
    } else {
        relayBadge.classList.add('bg-slate-50', 'text-slate-700');
        relayDot.classList.add('bg-gray-400');
    }

    const lastSeenValue = relayLastSeen || '-';
    if (relayRaw) relayRaw.innerText = lastSeenValue;
    if (relayReadable) {
        relayReadable.innerText = lastSeenValue !== '-' ? get_time_ago(lastSeenValue) : '-';
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
    pollingInterval = setInterval(function () {
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

// 글로벌 변수로 observer 선언
let folderTimeObserver = null;
let visibleFolderElements = new Set();

// Intersection Observer 초기화 함수
function initFolderTimeObserver() {
    // 기존 observer가 있으면 연결 해제
    if (folderTimeObserver) {
        folderTimeObserver.disconnect();
    }

    // 새 observer 생성
    folderTimeObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            const el = entry.target;

            if (entry.isIntersecting) {
                // 화면에 보이게 되면 Set에 추가
                visibleFolderElements.add(el);
            } else {
                // 화면에서 사라지면 Set에서 제거
                visibleFolderElements.delete(el);
            }
        });
    }, {
        root: null, // viewport 기준
        rootMargin: '50px', // viewport 여유공간
        threshold: 0.1 // 10% 이상 보일 때 감지
    });

    // 모든 폴더 타임라인 요소 감시 등록
    document.querySelectorAll('.monitored-folders-grid .toggle-text, .smb-folders-grid .toggle-text').forEach(el => {
        folderTimeObserver.observe(el);
    });
}

// 화면에 보이는 폴더만 시간 업데이트
function updateVisibleFolderTimes() {
    // 화면에 보이는 요소만 업데이트
    visibleFolderElements.forEach(el => {
        const timeString = el.querySelector('.time-string');
        const readableTime = el.querySelector('.readable-time');
        if (timeString && readableTime) {
            readableTime.innerText = get_time_ago(timeString.innerText);
        }
    });
}

window.onload = function () {
    getCurrentLogLevel();
    const logContent = document.getElementById('logContent');
    if (logContent) {
        logContent.scrollTop = logContent.scrollHeight;

        // 로그 영역에 마우스 진입/이탈 이벤트 리스너 추가
        logContent.addEventListener('mouseenter', function () {
            logHovered = true;
        });

        logContent.addEventListener('mouseleave', function () {
            logHovered = false;
        });

        // 로그 영역 스크롤 이벤트 감지
        let scrollTimeout;

        logContent.addEventListener('scroll', function () {
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

    const initialMonitorMode = document.body?.dataset?.monitorMode;
    if (initialMonitorMode) {
        monitorMode = initialMonitorMode;
    }

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

    // Intersection Observer 초기화
    initFolderTimeObserver();

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

        const relayLastSeenTime = document.querySelector('.relay-last-seen-string');
        const relayLastSeenReadable = document.querySelector('.relay-last-seen-readable');
        if (relayLastSeenTime && relayLastSeenReadable) {
            relayLastSeenReadable.innerText = relayLastSeenTime.innerText !== '-' ? get_time_ago(relayLastSeenTime.innerText) : '-';
        }

        // 화면에 보이는 폴더 요소만 수정 시간 업데이트
        updateVisibleFolderTimes();
    }, 1000);

    // 페이지 언로드 시 인터벌 정리
    window.addEventListener('beforeunload', function () {
        if (progressBarInterval) clearInterval(progressBarInterval);
        if (timeUpdateInterval) clearInterval(timeUpdateInterval);
        if (pollingInterval) clearInterval(pollingInterval);

        // Observer 연결 해제
        if (folderTimeObserver) {
            folderTimeObserver.disconnect();
        }

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
    const nfsWarning = document.getElementById('nfsWarning');

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

        // NFS 경고 메시지 표시 (polling 모드에서만 표시)
        if (nfsWarning) {
            if (monitorMode === 'polling') {
                nfsWarning.classList.remove('hidden');
            } else {
                nfsWarning.classList.add('hidden');
            }
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

function updateFolderNameScrolling(root = document) {
    // 애니메이션 기능 비활성화 (성능 문제로 인해 수동 스크롤로 대체됨)
}

// 폴더 목록 HTML 생성 함수 추가
function generateFolderListHtml(sortedFolders, showToggleButtons = true, action = 'mount') {

    let foldersHtml = '';

    // sortedFolders가 비어있거나 정의되지 않은 경우 처리
    if (!sortedFolders || (Array.isArray(sortedFolders) ? sortedFolders.length === 0 : Object.keys(sortedFolders).length === 0)) {
        return '<div class="text-center py-4"><p class="text-sm text-gray-600">폴더가 없습니다.</p></div>';
    }

    try {
        // sortedFolders가 객체인 경우 배열로 변환
        const foldersArray = Array.isArray(sortedFolders)
            ? sortedFolders
            : Object.entries(sortedFolders);

        if (!foldersArray.length) {
            return '<div class="text-center py-4"><p class="text-sm text-gray-600">폴더가 없습니다.</p></div>';
        }

        for (const entry of foldersArray) {
            const folder = entry[0];
            const info = entry[1];

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
                        <div class="folder-name-wrapper min-w-0 text-sm mb-1 font-medium text-gray-800">
                            <span class="folder-name-text">${folder}</span>
                        </div>
                        <div class="flex items-center toggle-text text-xs text-gray-500">
                            <svg class="w-3 h-3 mr-1" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
                            </svg>
                            <span class="readable-time">${info.mtime === '-' ? '수정시간 수집 중' : get_time_ago(info.mtime)}</span>
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
            updateUI(data);

            // 백그라운드로 폴더 목록 처리
            if (data.monitored_folders && Object.keys(data.monitored_folders).length > 0) {
                // mtime 기준으로 정렬된 폴더 배열 생성
                const sortedFolders = Object.entries(data.monitored_folders).sort((a, b) => {
                    return toSortableTimestamp(b[1].mtime) - toSortableTimestamp(a[1].mtime);
                });

                // 대규모 폴더 리스트 처리 최적화
                if (sortedFolders.length > 100) {
                    console.log(`대용량 폴더 목록 처리: ${sortedFolders.length}개`);
                    // 백그라운드에서 처리
                    setTimeout(() => {
                        window.requestAnimationFrame(() => {
                            updateFolderList(sortedFolders);
                        });
                    }, 10);
                } else {
                    // 일반적인 크기는 즉시 처리
                    window.requestAnimationFrame(() => {
                        updateFolderList(sortedFolders);
                    });
                }
            } else if (data.monitored_folders) {
                // 폴더가 없는 경우 빈 배열 전달
                window.requestAnimationFrame(() => {
                    updateFolderList([]);
                });
            }

            // 상태 표시기 숨기기 (이 위치로 이동)
            if (statusIndicator && (!data.monitored_folders || Object.keys(data.monitored_folders).length === 0)) {
                statusIndicator.classList.add('hidden');
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
function updateFolderList(sortedFolders) {
    // 초기 상태 수신 전에는 아무것도 하지 않음
    if (!hasReceivedStateUpdate) {
        return;
    }

    // 초기 스캔 중에는 빈 폴더 목록 메시지를 노출하지 않음
    // (캐시된 데이터가 있으면 표시하고, 빈 목록만 억제)
    if (initialScanInProgress && (!sortedFolders || sortedFolders.length === 0)) {
        return;
    }

    // 폴더 목록이 비어있는 경우
    if (!sortedFolders || sortedFolders.length === 0) {
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

        // 상태 표시기 숨기기
        const statusIndicator = document.querySelector('.status-update-indicator');
        if (statusIndicator) {
            statusIndicator.classList.add('hidden');
        }
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

    // 마운트된 폴더와 마운트되지 않은 폴더 분류 - 최적화 (한 번만 순회)
    const unmountedFolders = [];
    const mountedFolders = [];

    // 메모리 사용량 최적화: 배열 미리 할당 (대략적인 크기 예측)
    const estimatedSize = Math.min(1000, sortedFolders.length);
    unmountedFolders.length = estimatedSize;
    mountedFolders.length = estimatedSize;
    let unmountedCount = 0;
    let mountedCount = 0;

    // 각 폴더를 마운트 상태에 따라 분류 (정렬 순서 유지)
    for (let i = 0; i < sortedFolders.length; i++) {
        const folderEntry = sortedFolders[i];
        if (folderEntry[1].is_mounted) {
            mountedFolders[mountedCount++] = folderEntry;
        } else {
            unmountedFolders[unmountedCount++] = folderEntry;
        }
    }

    // 실제 사용된 크기로 배열 조정
    unmountedFolders.length = unmountedCount;
    mountedFolders.length = mountedCount;

    console.log('마운트되지 않은 폴더 갯수:', unmountedCount);
    console.log('마운트된 폴더 갯수:', mountedCount);

    // 총 폴더 수가 많은 경우 비동기 처리 최적화
    const isBatchProcessingNeeded = sortedFolders.length > 200;

    // 컨테이너 업데이트 함수 실행
    if (isBatchProcessingNeeded) {
        // 비동기적으로 컨테이너 업데이트 (타이밍 조정)
        setTimeout(() => {
            // NFS 패널에는 마운트되지 않은 폴더만 표시 (마운트 가능한 목록)
            updateFolderContainer('monitoredFoldersContainer', unmountedFolders, 'mount');

            // 다음 프레임에서 SMB 패널 업데이트
            setTimeout(() => {
                // SMB 패널에는 마운트된 폴더만 표시 (언마운트 가능한 목록)
                updateFolderContainer('smbFoldersContainer', mountedFolders, 'unmount');

                // 상태 표시기 숨기기
                const statusIndicator = document.querySelector('.status-update-indicator');
                if (statusIndicator) {
                    statusIndicator.classList.add('hidden');
                }
            }, 50);
        }, 0);
    } else {
        // 적은 수의 폴더는 일반적인 방식으로 처리
        updateFolderContainer('monitoredFoldersContainer', unmountedFolders, 'mount');
        updateFolderContainer('smbFoldersContainer', mountedFolders, 'unmount');

        // 상태 표시기 숨기기
        const statusIndicator = document.querySelector('.status-update-indicator');
        if (statusIndicator) {
            statusIndicator.classList.add('hidden');
        }
    }
}

// 폴더 컨테이너 업데이트 함수
function updateFolderContainer(containerId, folderData, action) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // 현재 컨테이너에 있는 모든 폴더 항목 가져오기
    const existingFolderItems = container.querySelectorAll('.folder-item');
    const existingFolderMap = new Map();

    // 기존 폴더 항목을 Map에 저장
    existingFolderItems.forEach(item => {
        existingFolderMap.set(item.dataset.folder, item);
    });

    // 폴더 데이터 준비 (정렬은 이미 완료됨)
    const folders = Array.isArray(folderData) ? folderData : Object.entries(folderData);

    // 처리된 폴더 경로 추적을 위한 Set
    const processedFolders = new Set();

    // 삭제할 항목을 담을 
    const removeList = [];

    // 성능 최적화를 위한 청크 단위 처리
    const CHUNK_SIZE = 50; // 한 번에 처리할 항목 수
    let currentChunk = 0;

    // 시간 변환 결과 캐싱
    const timeAgoCache = new Map();
    function getCachedTimeAgo(timestamp) {
        if (!timeAgoCache.has(timestamp)) {
            timeAgoCache.set(timestamp, get_time_ago(timestamp));
        }
        return timeAgoCache.get(timestamp);
    }

    // 지연 렌더링 함수 정의
    function processChunk() {
        // 현재 청크의 범위 계산
        const start = currentChunk * CHUNK_SIZE;
        const end = Math.min(start + CHUNK_SIZE, folders.length);

        // 해당 청크의 요소를 처리할 프래그먼트
        const newItemsFragment = document.createDocumentFragment();

        // 현재 청크의 폴더들 처리
        for (let i = start; i < end; i++) {
            const entry = folders[i];
            const folder = entry[0];
            const info = entry[1];

            // 폴더 처리 완료 표시
            processedFolders.add(folder);

            // 폴더 경로에서 안전한 ID 생성
            const folderId = `folder-${encodeURIComponent(folder).replace(/%/g, '_')}`;

            // 이미 존재하는 폴더 항목인지 확인
            if (existingFolderMap.has(folder)) {
                // 기존 항목 업데이트
                const existingItem = existingFolderMap.get(folder);

                // 수정 시간 업데이트 (변경된 경우에만)
                const timeString = existingItem.querySelector('.time-string');
                const readableTime = existingItem.querySelector('.readable-time');

                if (timeString && readableTime && timeString.innerText !== info.mtime) {
                    timeString.innerText = info.mtime;
                    readableTime.innerText = info.mtime === '-' ? '수정시간 수집 중' : getCachedTimeAgo(info.mtime);
                    existingItem.dataset.mtime = info.mtime;
                }
            } else {
                // 새 항목 생성
                const newItem = document.createElement('div');
                newItem.id = folderId;
                newItem.className = 'folder-item flex justify-between items-center p-2 mb-2 border border-gray-200 rounded-lg hover:bg-gray-50';
                newItem.dataset.folder = folder;
                newItem.dataset.mtime = info.mtime;
                newItem.dataset.action = action;

                // 렌더링 성능 향상을 위한 속성 추가
                newItem.style.contain = 'content';
                newItem.style.willChange = 'transform';

                // 버튼 텍스트와 스타일 결정
                let buttonText, buttonClass;
                if (action === 'mount') {
                    buttonText = '마운트';
                    buttonClass = 'bg-green-50 text-green-700 hover:bg-green-100';
                } else {
                    buttonText = '마운트 해제';
                    buttonClass = 'bg-red-50 text-red-700 hover:bg-red-100';
                }

                // 캐시된 시간 가져오기
                const cachedTimeAgo = info.mtime === '-' ? '수정시간 수집 중' : getCachedTimeAgo(info.mtime);

                newItem.innerHTML = `
                    <div class="flex-1 overflow-hidden">
                        <div class="folder-name-wrapper min-w-0 text-sm mb-1 font-medium text-gray-800">
                            <span class="folder-name-text">${folder}</span>
                        </div>
                        <div class="flex items-center toggle-text text-xs text-gray-500">
                            <svg class="w-3 h-3 mr-1" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
                            </svg>
                            <span class="readable-time">${cachedTimeAgo}</span>
                            <span class="hidden time-string">${info.mtime}</span>
                        </div>
                    </div>
                    <button onclick="toggleMount('${folder}', '${action}')" 
                        class="toggle-btn text-xs px-2 py-1 rounded ${buttonClass} transition-colors duration-200">
                        ${buttonText}
                    </button>
                `;

                // 새 항목 프래그먼트에 추가
                newItemsFragment.appendChild(newItem);
            }
        }

        // 새 항목들만 컨테이너에 추가 (기존 항목은 그대로 유지)
        container.appendChild(newItemsFragment);

        // updateFolderNameScrolling(newItemsFragment);

        // 새로 추가된 토글 텍스트에 클릭 이벤트 추가
        const newToggleElements = container.querySelectorAll('.folder-item:not([data-event-attached]) .toggle-text');
        newToggleElements.forEach(el => {
            el.addEventListener('click', () => {
                el.querySelector('.time-string').classList.toggle('hidden');
                el.querySelector('.readable-time').classList.toggle('hidden');
            });
            // 이벤트 추가 표시
            el.closest('.folder-item').dataset.eventAttached = 'true';
        });

        // 다음 청크로 이동
        currentChunk++;

        // 아직 처리할 청크가 남아있으면 다음 프레임에 스케줄링
        if (currentChunk * CHUNK_SIZE < folders.length) {
            // requestIdleCallback을 지원하면 사용, 아니면 requestAnimationFrame으로 폴백
            if (window.requestIdleCallback) {
                window.requestIdleCallback(processChunk, { timeout: 100 });
            } else {
                window.requestAnimationFrame(processChunk);
            }
        } else {
            // 모든 청크 처리 완료 후 제거할 항목 처리
            // 현재 데이터에 없는 기존 항목 제거
            for (const [folder, element] of existingFolderMap.entries()) {
                if (!processedFolders.has(folder)) {
                    removeList.push(element);
                }
            }

            // 제거할 항목이 있으면 일괄 처리
            if (removeList.length > 0) {
                // 실제 DOM에서 제거
                removeList.forEach(el => el.remove());
            }

            // 모든 처리가 완료되면 Observer 에 새 요소 등록
            updateObserversAfterProcessing();
        }
    }

    // 폴더 목록이 변경되면 Observer 업데이트
    function updateObserversAfterProcessing() {
        if (!folderTimeObserver) return;
        setTimeout(() => {
            // 새로 추가된 toggle-text 요소들 찾기
            const newToggleElements = container.querySelectorAll('.folder-item:not([data-observer-attached]) .toggle-text');

            // 각 요소를 Observer에 등록
            newToggleElements.forEach(el => {
                folderTimeObserver.observe(el);
                // 추적 중인 요소로 표시
                el.closest('.folder-item').dataset.observerAttached = 'true';
            });
        }, 0);
    }

    // 첫 번째 청크 처리 시작
    processChunk();
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

// ==========================================
// 트랜스코딩 설정 관련 함수
// ==========================================

let transcodingRules = [];

function toggleTranscodingPanel() {
    const panel = document.getElementById('transcodingPanel');
    const chevron = document.getElementById('transcodingChevron');
    if (!panel || !chevron) return;

    if (panel.classList.contains('hidden')) {
        panel.classList.remove('hidden');
        chevron.style.transform = 'rotate(180deg)';
        loadTranscodingConfig();
    } else {
        panel.classList.add('hidden');
        chevron.style.transform = 'rotate(0deg)';
    }
}

function loadTranscodingConfig() {
    fetch('/get_transcoding_config')
        .then(response => response.json())
        .then(data => {
            const enabledInput = document.getElementById('transcodingEnabled');
            if (enabledInput) enabledInput.checked = data.enabled || false;
            transcodingRules = data.rules || [];
            updateTranscodingStatus(data.enabled);
            renderTranscodingRules();
        })
        .catch(error => {
            console.error('트랜스코딩 설정 로드 오류:', error);
        });
}

function updateTranscodingStatus(enabled) {
    const statusEl = document.getElementById('transcodingStatus');
    if (statusEl) {
        if (enabled) {
            statusEl.textContent = 'ON';
            statusEl.className = 'text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700';
        } else {
            statusEl.textContent = 'OFF';
            statusEl.className = 'text-xs px-2 py-0.5 rounded-full bg-slate-50 text-slate-600';
        }
    }
}

function renderTranscodingRules() {
    const container = document.getElementById('transcodingRules');
    const emptyMsg = document.getElementById('transcodingEmpty');
    if (!container || !emptyMsg) return;

    if (transcodingRules.length === 0) {
        container.innerHTML = '';
        emptyMsg.classList.remove('hidden');
        return;
    }

    emptyMsg.classList.add('hidden');

    let html = '';
    transcodingRules.forEach((rule) => {
        const extensions = (rule.file_extensions || []).join(', ');
        html += `
            <div class="bg-gray-50 rounded-lg p-3 border border-gray-200">
                <div class="flex items-center justify-between mb-2">
                    <span class="text-sm font-medium text-gray-800">${escapeHtml(rule.name || '이름 없는 규칙')}</span>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs text-gray-700">
                    <div><span class="text-gray-500">폴더 패턴:</span> ${escapeHtml(rule.folder_pattern || '-')}</div>
                    <div><span class="text-gray-500">파일 확장자:</span> ${escapeHtml(extensions || '-')}</div>
                    <div><span class="text-gray-500">원본 삭제:</span> ${rule.delete_original !== false ? '예' : '아니오'}</div>
                    <div class="md:col-span-2"><span class="text-gray-500">FFmpeg 옵션:</span> <code class="text-[11px]">${escapeHtml(rule.ffmpeg_options || '-')}</code></div>
                    <div class="md:col-span-2"><span class="text-gray-500">출력 패턴:</span> ${escapeHtml(rule.output_pattern || '{{filename}}.transcoded.{{ext}}')}</div>
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

function addTranscodingRule() {
    transcodingRules.push({
        name: `규칙 ${transcodingRules.length + 1}`,
        folder_pattern: '',
        file_extensions: ['.mp4'],
        ffmpeg_options: '-c:v copy -c:a aac',
        delete_original: true,
        output_pattern: '{{filename}}.transcoded.{{ext}}'
    });
    renderTranscodingRules();
    saveTranscodingConfig();
}

function removeTranscodingRule(index) {
    if (confirm('이 규칙을 삭제하시겠습니까?')) {
        transcodingRules.splice(index, 1);
        renderTranscodingRules();
        saveTranscodingConfig();
    }
}

function updateRule(index, field, value) {
    transcodingRules[index][field] = value;
    saveTranscodingConfig();
}

function updateRuleExtensions(index, value) {
    transcodingRules[index].file_extensions = value
        .split(',')
        .map(ext => ext.trim())
        .filter(ext => ext.length > 0);
    saveTranscodingConfig();
}

function saveTranscodingConfig() {
    const enabledInput = document.getElementById('transcodingEnabled');
    const enabled = enabledInput ? enabledInput.checked : false;
    updateTranscodingStatus(enabled);

    fetch('/update_transcoding_config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            enabled: enabled,
            rules: transcodingRules
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status !== 'success') {
                console.error('트랜스코딩 설정 저장 실패:', data.message);
            }
        })
        .catch(error => {
            console.error('트랜스코딩 설정 저장 오류:', error);
        });
}

// 페이지 로드 시 트랜스코딩 상태 표시 업데이트
document.addEventListener('DOMContentLoaded', function () {
    fetch('/get_transcoding_config')
        .then(response => response.json())
        .then(data => {
            updateTranscodingStatus(data.enabled || false);
            transcodingRules = data.rules || [];
            renderTranscodingRules();
        })
        .catch(() => { });

    // 새로고침 후 스캔 진행 중이면 UI 복구
    fetch('/get_scan_status')
        .then(response => response.json())
        .then(data => {
            if (data.is_processing || (data.phase && data.phase !== 'idle')) {
                const progressDiv = document.getElementById('transcodingProgress');
                const scanBtn = document.getElementById('scanTranscodingBtn');
                const cancelBtn = document.getElementById('cancelScanBtn');
                if (progressDiv) progressDiv.classList.remove('hidden');
                if (data.is_processing) {
                    if (scanBtn) { scanBtn.disabled = true; scanBtn.classList.add('opacity-50', 'cursor-not-allowed'); }
                    if (cancelBtn) cancelBtn.classList.remove('hidden');
                }
                updateProgressUI(data);

                // 스캔 중이면 폴링으로 상태 갱신 (SocketIO 연결 전 대비)
                if (data.is_processing) {
                    const pollInterval = setInterval(() => {
                        fetch('/get_scan_status')
                            .then(r => r.json())
                            .then(s => {
                                updateProgressUI(s);
                                if (!s.is_processing) {
                                    clearInterval(pollInterval);
                                    resetScanUI();
                                }
                            })
                            .catch(() => clearInterval(pollInterval));
                    }, 1500);
                }
            }
        })
        .catch(() => { });
});

// 트랜스코딩 스캔 시작
function startTranscodingScan() {
    if (!confirm('기존 폴더를 스캔하여 트랜스코딩을 시작하시겠습니까?')) return;

    const scanBtn = document.getElementById('scanTranscodingBtn');
    const cancelBtn = document.getElementById('cancelScanBtn');
    const progressDiv = document.getElementById('transcodingProgress');

    if (scanBtn) {
        scanBtn.disabled = true;
        scanBtn.classList.add('opacity-50', 'cursor-not-allowed');
    }
    if (cancelBtn) cancelBtn.classList.remove('hidden');
    if (progressDiv) progressDiv.classList.remove('hidden');

    // 진행상황 초기화
    updateProgressUI({ phase: 'scanning', total_files: 0, current_index: 0, current_file: '', completed: 0, failed: 0, message: '폴더 스캔 중...' });

    fetch('/scan_transcoding', { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            if (data.status !== 'success') {
                alert('오류: ' + data.message);
                resetScanUI();
            }
        })
        .catch(error => {
            console.error('스캔 시작 오류:', error);
            alert('스캔 시작 중 오류가 발생했습니다.');
            resetScanUI();
        });
}

function cancelTranscodingScan() {
    fetch('/cancel_transcoding_scan', { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            console.log('스캔 취소:', data.message);
        })
        .catch(error => {
            console.error('스캔 취소 오류:', error);
        });
}

function handleTranscodingProgress(data) {
    updateProgressUI(data);

    if (data.phase === 'done' || data.phase === 'error') {
        resetScanUI();
    }
}

function updateProgressUI(data) {
    const msgEl = document.getElementById('transcodingProgressMsg');
    const percentEl = document.getElementById('transcodingProgressPercent');
    const barEl = document.getElementById('transcodingProgressBar');
    const fileEl = document.getElementById('transcodingProgressFile');
    const completedEl = document.getElementById('transcodingCompleted');
    const failedEl = document.getElementById('transcodingFailed');
    const currentEl = document.getElementById('transcodingCurrent');
    const totalEl = document.getElementById('transcodingTotal');

    msgEl.textContent = data.message || '';
    fileEl.textContent = data.current_file || '';
    completedEl.textContent = data.completed || 0;
    failedEl.textContent = data.failed || 0;
    currentEl.textContent = data.current_index || 0;
    totalEl.textContent = data.total_files || 0;

    const total = data.total_files || 0;
    const current = data.current_index || 0;
    const percent = total > 0 ? Math.round((current / total) * 100) : 0;

    percentEl.textContent = percent + '%';
    barEl.style.width = percent + '%';

    // 상태에 따른 바 색상
    if (data.phase === 'done') {
        barEl.className = 'h-full rounded-full bg-emerald-500 transition-all duration-300';
        barEl.style.width = '100%';
        percentEl.textContent = '완료';
    } else if (data.phase === 'error') {
        barEl.className = 'h-full rounded-full bg-red-500 transition-all duration-300';
        percentEl.textContent = '오류';
    } else if (data.phase === 'scanning') {
        barEl.className = 'h-full rounded-full bg-yellow-400 transition-all duration-300 animate-pulse';
        barEl.style.width = '100%';
        percentEl.textContent = '스캔 중';
    } else {
        barEl.className = 'h-full rounded-full bg-blue-500 transition-all duration-300';
    }
}

function resetScanUI() {
    const scanBtn = document.getElementById('scanTranscodingBtn');
    const cancelBtn = document.getElementById('cancelScanBtn');

    if (scanBtn) {
        scanBtn.disabled = false;
        scanBtn.classList.remove('opacity-50', 'cursor-not-allowed');
    }
    if (cancelBtn) cancelBtn.classList.add('hidden');
}


// 기능 토글 함수
function toggleFeature(feature, enabled) {
    console.log(`Toggling feature ${feature} to ${enabled}`);

    // 체크박스 ID (feature 이름의 _를 -로 변환)
    const checkboxId = `toggle-${feature.replace('_', '-')}`;
    const checkbox = document.getElementById(checkboxId);
    if (checkbox) checkbox.disabled = true;

    fetch('/api/toggle_feature', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ feature: feature, enabled: enabled }),
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            console.log(`${feature} update success`);
            // UI 업데이트는 소켓/폴링을 통해 수행됨
        } else {
            console.error(`${feature} update failed: ${data.message}`);
            alert(`설정 변경 실패: ${data.message}`);
            // 체크박스 상태 복구
            if (checkbox) checkbox.checked = !enabled;
        }
    })
    .catch(error => {
        console.error('Error toggling feature:', error);
        alert('설정 변경 중 오류가 발생했습니다.');
        // 체크박스 상태 복구
        if (checkbox) checkbox.checked = !enabled;
    })
    .finally(() => {
        if (checkbox) checkbox.disabled = false;
    });
}

function escapeHtml(text) {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return String(text || '').replace(/[&<>"']/g, m => map[m]);
}
