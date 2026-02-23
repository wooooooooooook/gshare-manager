import logging
import os
import subprocess
import shlex
import queue
import threading
from functools import lru_cache
from typing import Optional, Dict, Any, List, Callable
from config import GshareConfig  # type: ignore


class Transcoder:
    """폴더 내 미디어 파일에 대해 ffmpeg 트랜스코딩을 수행하는 클래스"""

    def __init__(self, config: GshareConfig):
        self.config = config
        self.enabled = config.TRANSCODING_ENABLED
        self.rules = config.TRANSCODING_RULES or []
        self.done_filename = config.TRANSCODING_DONE_FILENAME
        self._build_optimized_rules()
        self._processing = False  # 중복 실행 방지 플래그
        self._scan_cancel = False  # 스캔 취소 플래그
        self.scan_status: Dict[str, Any] = {'phase': 'idle'}  # 스캔 상태 (새로고침 복구용)

        # 비동기 처리를 위한 큐와 스레드 초기화
        self.task_queue = queue.Queue()
        self._lock = threading.Lock()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _worker_loop(self):
        """백그라운드에서 트랜스코딩 작업을 처리하는 루프"""
        while True:
            try:
                folder_path = self.task_queue.get()
                # 수동 스캔과 충돌 방지를 위해 락 사용
                with self._lock:
                    self._process_folder_sync(folder_path)
            except Exception as e:
                logging.error(f"트랜스코딩 워커 스레드 오류: {e}")
            finally:
                self.task_queue.task_done()

    def reload_config(self, config: GshareConfig):
        """설정 변경 시 규칙을 다시 로드"""
        self.config = config
        self.enabled = config.TRANSCODING_ENABLED
        self.rules = config.TRANSCODING_RULES or []
        self.done_filename = config.TRANSCODING_DONE_FILENAME
        self._build_optimized_rules()
    def _build_optimized_rules(self):
        """규칙의 확장자를 미리 정규화하여 최적화된 구조 생성"""
        self._optimized_rules = []
        if not self.rules:
            return

        for rule in self.rules:
            folder_pattern = rule.get('folder_pattern', '')
            file_extensions = rule.get('file_extensions', [])

            normalized_extensions = set()
            if file_extensions:
                for e in file_extensions:
                    ext = e.lower()
                    if not ext.startswith('.'):
                        ext = f'.{ext}'
                    normalized_extensions.add(ext)

            self._optimized_rules.append({
                'original': rule,
                'folder_pattern': folder_pattern,
                'extensions': normalized_extensions
            })

    def _get_active_rules_for_folder(self, root: str) -> List[tuple]:
        """
        해당 폴더(root)에 대해 검사해야 할 규칙 목록을 최적화하여 반환.
        Returns: list of (optimized_rule, check_filename_only)
        """
        if not getattr(self, '_optimized_rules', None):
            return []

        active = []
        for optimized in self._optimized_rules:
            folder_pattern = optimized['folder_pattern']
            if not folder_pattern:
                continue

            # 폴더 경로에 패턴이 이미 포함되어 있으면 파일명 검사 불필요 (check_filename_only=False)
            if folder_pattern in root:
                active.append((optimized, False))
            else:
                # 폴더 경로에 없으면 파일명에 포함될 수 있으므로 검사 필요 (check_filename_only=True)
                active.append((optimized, True))
        return active

    def _match_rule_for_filename(self, filename: str, active_rules: List[tuple]) -> Optional[Dict[str, Any]]:
        """폴더별 활성 규칙 목록에서 파일명/확장자에 맞는 규칙을 반환"""
        ext = None  # Lazy loading: 확장자는 필요할 때만 계산

        for optimized, check_filename_only in active_rules:
            if check_filename_only and optimized['folder_pattern'] not in filename:
                continue

            if optimized['extensions']:
                if ext is None:
                    _, ext = os.path.splitext(filename)
                    ext = ext.lower()

                if ext not in optimized['extensions']:
                    continue

            return optimized['original']

        return None

    def _is_skippable_file(self, filename: str, done_set: Any) -> bool:
        """스캔/트랜스코딩 공통 건너뛰기 조건"""
        if filename == self.done_filename:
            return True
        if filename.endswith('.tmp') or '.transcoding_tmp.' in filename:
            return True
        if filename in done_set:
            return True
        return False

    def _iter_walk_matches(self, scan_root: str, log_prefix: str = ""):
        """os.walk 기반으로 규칙 매칭된 파일을 순회"""
        for root, dirs, files in os.walk(scan_root, followlinks=True):
            dirs[:] = [d for d in dirs if not d.startswith('@') and not d.startswith('.')]

            if files:
                logging.info(f"{log_prefix}디렉토리 진입: {root} (검색 대상 파일 수: {len(files)})")

            # Optimization: check if done_file is in files list to avoid syscall
            if self.done_filename in files:
                done_set = self._load_done_list(root)
            else:
                done_set = frozenset()

            active_rules = self._get_active_rules_for_folder(root)

            for filename in files:
                if self._is_skippable_file(filename, done_set):
                    continue

                rule = self._match_rule_for_filename(filename, active_rules)
                if rule is None:
                    continue

                yield root, filename, rule

    def _iter_known_folder_matches(self, folder_path: str, subfolders: List[str]):
        """이미 파악된 폴더 목록(폴더 스캔 결과)을 재사용해 파일만 확인"""
        for sub in subfolders:
            full_path = os.path.join(folder_path, sub)
            if not os.path.isdir(full_path):
                continue

            try:
                done_set = self._load_done_list(full_path)
                active_rules = self._get_active_rules_for_folder(full_path)

                with os.scandir(full_path) as entries:
                    for entry in entries:
                        if not entry.is_file():
                            continue

                        filename = entry.name
                        if self._is_skippable_file(filename, done_set):
                            continue

                        rule = self._match_rule_for_filename(filename, active_rules)
                        if rule is None:
                            continue

                        yield full_path, filename, rule
            except OSError as e:
                logging.debug(f"폴더 재사용 스캔 실패(무시): {full_path} - {e}")

    def find_matching_rule(self, file_path: str) -> Optional[Dict[str, Any]]:
        """파일 경로가 매칭되는 트랜스코딩 규칙을 찾아 반환"""
        if not self.enabled or not getattr(self, '_optimized_rules', None):
            return None

        _, ext = os.path.splitext(file_path)
        ext = ext.lower()

        for optimized in self._optimized_rules:
            folder_pattern = optimized['folder_pattern']
            extensions = optimized['extensions']

            if not folder_pattern:
                continue

            # 폴더 패턴 매칭 (경로에 패턴 문자열이 포함되어 있는지 확인)
            if folder_pattern not in file_path:
                continue

            # 확장자 매칭 (set 조회 O(1))
            if extensions and ext not in extensions:
                continue

            return optimized['original']

        return None

    def _find_rule_for_scan(self, file_path: str) -> Optional[Dict[str, Any]]:
        """수동 스캔용 규칙 매칭 - enabled 여부와 무관하게 동작"""
        if not getattr(self, '_optimized_rules', None):
            return None

        _, ext = os.path.splitext(file_path)
        ext = ext.lower()

        for optimized in self._optimized_rules:
            folder_pattern = optimized['folder_pattern']
            extensions = optimized['extensions']

            if not folder_pattern:
                continue
            if folder_pattern not in file_path:
                continue

            # 확장자 매칭 (set 조회 O(1))
            if extensions and ext not in extensions:
                continue
            return optimized['original']
        return None

    @lru_cache(maxsize=1024)
    def _read_done_file_content(self, filepath: str, mtime: float) -> frozenset:
        """Cache the content of .transcoding_done file based on mtime"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return frozenset(line.strip() for line in f if line.strip())
        except Exception:
            return frozenset()

    def _load_done_list(self, directory: str) -> frozenset:
        """디렉토리의 .transcoding_done 파일에서 이미 처리된 파일 목록을 로드"""
        done_file = os.path.join(directory, self.done_filename)
        try:
            stat_result = os.stat(done_file)
            return self._read_done_file_content(done_file, stat_result.st_mtime)
        except (FileNotFoundError, OSError):
            return frozenset()

    def _mark_done(self, directory: str, filename: str):
        """ファイルを .transcoding_done에 기록"""
        done_file = os.path.join(directory, self.done_filename)
        try:
            with open(done_file, 'a', encoding='utf-8') as f:
                f.write(f"{filename}\n")
        except Exception as e:
            logging.error(f"처리 완료 기록 실패 ({directory}): {e}")

    def process_folder(self, folder_path: str) -> int:
        """폴더 트랜스코딩 요청을 큐에 추가 (비동기 처리)"""
        if not self.enabled or not self.rules:
            return 0

        self.task_queue.put(folder_path)
        logging.info(f"트랜스코딩 작업 큐에 추가됨: {folder_path}")
        return 0  # 비동기 처리이므로 즉시 반환

    def _process_folder_sync(self, folder_path: str) -> int:
        """폴더 내 매칭되는 파일들을 트랜스코딩 (동기 실행). 처리된 파일 수 반환."""
        if not self.enabled or not self.rules:
            return 0

        # _processing 플래그는 상태 표시용으로만 사용 (동기화는 _lock으로 처리됨)
        self._processing = True
        processed_count = 0

        try:
            if not os.path.exists(folder_path):
                logging.warning(f"트랜스코딩 대상 폴더가 존재하지 않습니다: {folder_path}")
                return 0

            for root, filename, rule in self._iter_walk_matches(folder_path):
                    file_path = os.path.join(root, filename)

                    # 출력 패턴 기반 건너뛰기 (보조)
                    output_pattern = rule.get('output_pattern', '{{filename}}.transcoded.{{ext}}')
                    if '{{filename}}' in output_pattern:
                        pattern_parts = output_pattern.replace('{{ext}}', '').replace('{{filename}}', '')
                        if pattern_parts and pattern_parts in filename:
                            continue

                    success = self.transcode_file(file_path, rule)
                    if success:
                        self._mark_done(root, filename)
                        processed_count += 1

        except Exception as e:
            logging.error(f"폴더 트랜스코딩 중 오류 발생 ({folder_path}): {e}")
        finally:
            self._processing = False

        if processed_count > 0:
            logging.info(f"트랜스코딩 완료: {folder_path} ({processed_count}개 파일 처리)")

        return processed_count

    def _apply_output_pattern(self, file_name: str, file_ext: str, pattern: str) -> str:
        """출력 파일명 패턴을 적용하여 최종 파일명을 생성
        
        지원 변수:
            {{filename}} - 원본 파일명 (확장자 제외)
            {{ext}} - 원본 확장자 (점 제외)
        """
        ext_no_dot = file_ext.lstrip('.')
        result = pattern.replace('{{filename}}', file_name)
        result = result.replace('{{ext}}', ext_no_dot)
        return result

    def transcode_file(self, file_path: str, rule: Dict[str, Any]) -> bool:
        """개별 파일에 대해 ffmpeg 트랜스코딩 수행"""
        rule_name = rule.get('name', '알 수 없는 규칙')
        ffmpeg_options = rule.get('ffmpeg_options', '')
        delete_original = rule.get('delete_original', True)
        output_pattern = rule.get('output_pattern', '{{filename}}.transcoded.{{ext}}')

        if not ffmpeg_options:
            logging.warning(f"ffmpeg 옵션이 비어있습니다. 규칙: {rule_name}")
            return False

        # 임시 출력 파일 경로 생성
        file_dir = os.path.dirname(file_path)
        file_name, file_ext = os.path.splitext(os.path.basename(file_path))
        tmp_path = os.path.join(file_dir, f"{file_name}.transcoding_tmp{file_ext}")

        try:
            logging.info(f"트랜스코딩 시작: {file_path} (규칙: {rule_name})")

            # ffmpeg 명령어 구성
            cmd = ['ffmpeg', '-y', '-i', file_path]
            # 메타데이터 보존 (-map_metadata 0) - 사용자가 명시하지 않은 경우 기본 추가
            if '-map_metadata' not in ffmpeg_options:
                cmd.extend(['-map_metadata', '0'])
            # ffmpeg 옵션을 안전하게 분리
            cmd.extend(shlex.split(ffmpeg_options))
            cmd.append(tmp_path)

            logging.debug(f"ffmpeg 명령어: {' '.join(cmd)}")

            # ffmpeg 실행
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600  # 1시간 타임아웃
            )

            if result.returncode != 0:
                logging.error(f"트랜스코딩 실패: {file_path}")
                logging.error(f"ffmpeg stderr: {result.stderr[-500:]}")
                # 임시 파일 정리
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                return False

            # 임시 파일이 제대로 생성되었는지 확인
            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                logging.error(f"트랜스코딩 출력 파일이 비어있거나 존재하지 않습니다: {tmp_path}")
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                return False

            # 원본 파일 권한/소유자 정보 보존 시도
            try:
                stat_info = os.stat(file_path)
            except OSError:
                stat_info = None

            # 출력 파일명 결정
            output_filename = self._apply_output_pattern(file_name, file_ext, output_pattern)
            output_path = os.path.join(file_dir, output_filename)

            if delete_original:
                # 원본 파일을 변환 파일로 대체
                os.replace(tmp_path, output_path)
                # 출력 파일명이 원본과 다르면 원본 삭제
                if output_path != file_path and os.path.exists(file_path):
                    os.remove(file_path)
                logging.info(f"트랜스코딩 완료 (원본 대체): {output_path}")
            else:
                # 원본 유지, 변환 파일은 패턴에 따라 저장
                os.replace(tmp_path, output_path)
                logging.info(f"트랜스코딩 완료 (별도 저장): {output_path}")

            # 권한 복원 시도
            if stat_info:
                try:
                    target = file_path if delete_original else output_path
                    os.chmod(target, stat_info.st_mode)
                    os.chown(target, stat_info.st_uid, stat_info.st_gid)
                    # 파일 접근/수정 시간 복원
                    os.utime(target, (stat_info.st_atime, stat_info.st_mtime))
                except OSError as e:
                    logging.debug(f"파일 권한/소유자 복원 실패 (무시): {e}")

            return True

        except subprocess.TimeoutExpired:
            logging.error(f"트랜스코딩 타임아웃 (1시간 초과): {file_path}")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return False
        except Exception as e:
            logging.error(f"트랜스코딩 오류: {file_path} - {e}")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return False

    def get_rules_summary(self) -> List[Dict[str, Any]]:
        """현재 트랜스코딩 규칙 요약 반환"""
        return [{
            'name': rule.get('name', ''),
            'folder_pattern': rule.get('folder_pattern', ''),
            'file_extensions': rule.get('file_extensions', []),
            'ffmpeg_options': rule.get('ffmpeg_options', ''),
            'delete_original': rule.get('delete_original', True),
            'output_pattern': rule.get('output_pattern', '{{filename}}.transcoded.{{ext}}')
        } for rule in self.rules]

    def collect_matching_files(self, folder_path: str, subfolders: Optional[List[str]] = None, progress_callback: Optional[Callable] = None) -> List[Dict[str, Any]]:
        """폴더에서 규칙에 매칭되는 파일 목록을 수집 (enabled 무관, 수동 스캔용)
        
        Args:
            folder_path: 베이스 마운트 경로 (/mnt/gshare)
            subfolders: 이미 파악된 서브폴더 목록 (선택 사항)
            progress_callback: 진행 상황 전송용 콜백
        """
        matched_files = []
        if not self.rules:
            logging.warning("트랜스코딩 규칙이 없습니다. 설정에서 규칙을 추가하세요.")
            return matched_files
        if not os.path.exists(folder_path):
            logging.warning(f"스캔 대상 경로가 존재하지 않습니다: {folder_path}")
            return matched_files

        logging.info(f"파일 수집 시작: {folder_path} (서브폴더 지정: {len(subfolders) if subfolders else '전체 스캔'})")
        
        # 0. 진행 상황 초기 업데이트
        if progress_callback:
            progress_callback({
                'phase': 'scanning',
                'message': '스캔 대상 폴더 필터링 중...',
                'total_files': 0, 'current_index': 0, 'completed': 0, 'failed': 0
            })

        # 1. 스캔할 폴더 목록 필터링 (최적화)
        active_patterns = [r.get('folder_pattern') for r in self.rules if r.get('folder_pattern')]
        scan_roots = []
        
        if subfolders:
            # 설정된 규칙의 패턴이 포함된 폴더만 골라냄
            filtered_subs = []
            for sub in subfolders:
                # 윈도우/리눅스 경로 구분자 호환성을 위해 /로 통일하여 비교
                normalized_sub = sub.replace(os.sep, '/')
                if any(p in normalized_sub for p in active_patterns):
                    filtered_subs.append(sub)
            
            logging.info(f"폴더 필터링 완료: {len(subfolders)}개 중 {len(filtered_subs)}개 폴더가 규칙에 매칭됨")
            
            # 최적화: 중복 스캔 방지 (상위 폴더가 포함되어 있으면 하위 폴더 제외)
            filtered_subs.sort()
            unique_subs = []
            if filtered_subs:
                unique_subs.append(filtered_subs[0])
                for i in range(1, len(filtered_subs)):
                    prev = unique_subs[-1]
                    curr = filtered_subs[i]
                    # prev가 curr의 상위 폴더인지 확인 (curr이 prev + os.sep로 시작하거나 동일한 경우)
                    if curr == prev or curr.startswith(prev + os.sep):
                        continue
                    unique_subs.append(curr)

            if len(unique_subs) < len(filtered_subs):
                logging.info(f"중복/하위 폴더 제거됨: {len(filtered_subs)} -> {len(unique_subs)}개 (최적화)")

            for sub in unique_subs:
                full_path = os.path.join(folder_path, sub)
                if os.path.exists(full_path):
                    scan_roots.append(full_path)
        else:
            scan_roots = [folder_path]

        # 2. 파일 수집 실행
        for i, scan_root in enumerate(scan_roots):
            if hasattr(self, '_scan_cancel') and self._scan_cancel:
                break

            # 진행 상황 업데이트 (UI에서 0/0으로 멈춰 보이지 않게 함)
            if progress_callback:
                rel_name = os.path.relpath(scan_root, folder_path)
                progress_callback({
                    'phase': 'scanning',
                    'message': f'폴더 스캔 중 ({i+1}/{len(scan_roots)}) - {len(matched_files)}개 발견: {rel_name}',
                    'total_files': len(matched_files),
                    'current_index': 0, 'completed': 0, 'failed': 0
                })

            # walk를 하되, 필터링된 폴더의 직계 파일만 보거나 하위까지 봄
            # subfolders 리스트가 이미 leaf라면 walk는 한 번만 돌고 끝남
            iterator = self._iter_walk_matches(scan_root, log_prefix="[수동 스캔] ")
            if subfolders:
                rel_root = os.path.relpath(scan_root, folder_path)
                iterator = self._iter_known_folder_matches(folder_path, [rel_root])

            for root, filename, rule in iterator:
                logging.info(f"대상 파일 발견: {filename} (규칙: {rule.get('name')})")
                matched_files.append({
                    'file_path': os.path.join(root, filename),
                    'filename': filename,
                    'folder': root,
                    'rule': rule,
                    'rule_name': rule.get('name', '')
                })

        logging.info(f"파일 수집 완료: {len(matched_files)}개 발견")
        return matched_files

    def scan_all_folders(self, mount_path: str, progress_callback: Optional[Callable] = None, subfolders: Optional[List[str]] = None) -> Dict[str, Any]:
        """전체 마운트 경로를 스캔하여 매칭되는 파일들을 트랜스코딩.
        
        progress_callback: callable(status_dict) - 진행 상황 콜백
            status_dict 형식:
            {
                'phase': 'scanning' | 'transcoding' | 'done' | 'error',
                'total_files': int,
                'current_index': int,  # 1-based
                'current_file': str,
                'completed': int,
                'failed': int,
                'message': str
            }
        """
        # 락 획득 시도 (비동기 트랜스코딩과 충돌 방지)
        if not self._lock.acquire(blocking=False):
            if progress_callback:
                progress_callback({
                    'phase': 'error',
                    'message': '자동 트랜스코딩 작업이 진행 중입니다. 잠시 후 다시 시도해주세요.'
                })
            return {'completed': 0, 'failed': 0, 'total': 0}

        if self._processing:
            # 락을 획득했지만 _processing이 True라면 뭔가 잘못된 상태이나 일단 해제하고 리턴
            self._lock.release()
            if progress_callback:
                progress_callback({
                    'phase': 'error',
                    'message': '트랜스코딩이 이미 진행 중입니다.'
                })
            return {'completed': 0, 'failed': 0, 'total': 0}

        # 진단 로그
        logging.info("=== 수동 스캔 시작 ===")
        logging.info(f"마운트 경로: {mount_path}")
        logging.info(f"경로 존재 여부: {os.path.exists(mount_path)}")
        logging.info(f"규칙 수: {len(self.rules)}")
        for i, rule in enumerate(self.rules):
            logging.info(f"  규칙 {i+1}: name={rule.get('name')}, folder_pattern={rule.get('folder_pattern')}, extensions={rule.get('file_extensions')}")

        self._processing = True
        self._scan_cancel = False
        completed = 0
        failed = 0
        all_matched = []

        def _emit(status: Dict[str, Any]):
            """progress_callback 호출 + scan_status 업데이트"""
            self.scan_status = status
            if progress_callback:
                try:
                    progress_callback(status)
                except Exception as cb_err:
                    logging.error(f"progress_callback 오류: {cb_err}")

        try:
            # 1단계: 파일 수집
            _emit({
                'phase': 'scanning',
                'total_files': 0,
                'current_index': 0,
                'current_file': '',
                'completed': 0,
                'failed': 0,
                'message': '폴더 목록 수집 중...'
            })

            all_matched = self.collect_matching_files(mount_path, subfolders=subfolders, progress_callback=_emit)
            total = len(all_matched)

            if total == 0:
                _emit({
                    'phase': 'done',
                    'total_files': 0,
                    'current_index': 0,
                    'current_file': '',
                    'completed': 0,
                    'failed': 0,
                    'message': '트랜스코딩할 파일이 없습니다. (규칙 확인 필요)'
                })
                return {'completed': 0, 'failed': 0, 'total': 0}

            logging.info(f"수동 스캔: {total}개 파일 발견")

            # 2단계: 순차 트랜스코딩
            for idx, item in enumerate(all_matched):
                if self._scan_cancel:
                    logging.info("스캔 취소됨")
                    _emit({
                        'phase': 'done',
                        'total_files': total,
                        'current_index': idx,
                        'current_file': '',
                        'completed': completed,
                        'failed': failed,
                        'message': f'스캔 취소됨 (완료: {completed}, 실패: {failed})'
                    })
                    break

                file_path = item['file_path']
                filename = item['filename']
                rule = item['rule']

                _emit({
                    'phase': 'transcoding',
                    'total_files': total,
                    'current_index': idx + 1,
                    'current_file': filename,
                    'completed': completed,
                    'failed': failed,
                    'message': f'트랜스코딩 중 ({idx+1}/{total}): {filename}'
                })

                success = self.transcode_file(file_path, rule)
                if success:
                    self._mark_done(item.get('folder', os.path.dirname(file_path)), filename)
                    completed += 1
                else:
                    failed += 1

            # 완료
            if not self._scan_cancel:
                msg = f'스캔 완료! 완료: {completed}, 실패: {failed}, 전체: {total}'
                logging.info(msg)
                _emit({
                    'phase': 'done',
                    'total_files': total,
                    'current_index': total,
                    'current_file': '',
                    'completed': completed,
                    'failed': failed,
                    'message': msg
                })

        except Exception as e:
            import traceback
            logging.error(f"수동 스캔 중 오류: {e}\n{traceback.format_exc()}")
            _emit({
                'phase': 'error',
                'total_files': len(all_matched),
                'current_index': 0,
                'current_file': '',
                'completed': completed,
                'failed': failed,
                'message': f'오류 발생: {str(e)}'
            })
        finally:
            self._processing = False
            self._scan_cancel = False
            self._lock.release()

        return {'completed': completed, 'failed': failed, 'total': len(all_matched)}

    def cancel_scan(self):
        """진행 중인 스캔 취소"""
        self._scan_cancel = True
