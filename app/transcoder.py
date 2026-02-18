import logging
import os
import subprocess
import shlex
from typing import Optional, Dict, Any, List, Callable
from config import GshareConfig  # type: ignore


class Transcoder:
    """폴더 내 미디어 파일에 대해 ffmpeg 트랜스코딩을 수행하는 클래스"""

    def __init__(self, config: GshareConfig):
        self.config = config
        self.enabled = config.TRANSCODING_ENABLED
        self.rules = config.TRANSCODING_RULES or []
        self._processing = False  # 중복 실행 방지 플래그
        self._scan_cancel = False  # 스캔 취소 플래그
        self.scan_status: Dict[str, Any] = {'phase': 'idle'}  # 스캔 상태 (새로고침 복구용)

    def reload_config(self, config: GshareConfig):
        """설정 변경 시 규칙을 다시 로드"""
        self.config = config
        self.enabled = config.TRANSCODING_ENABLED
        self.rules = config.TRANSCODING_RULES or []

    def find_matching_rule(self, file_path: str) -> Optional[Dict[str, Any]]:
        """파일 경로가 매칭되는 트랜스코딩 규칙을 찾아 반환"""
        if not self.enabled or not self.rules:
            return None

        for rule in self.rules:
            folder_pattern = rule.get('folder_pattern', '')
            file_extensions = rule.get('file_extensions', [])

            if not folder_pattern:
                continue

            # 폴더 패턴 매칭 (경로에 패턴 문자열이 포함되어 있는지 확인)
            if folder_pattern not in file_path:
                continue

            # 확장자 매칭
            if file_extensions:
                _, ext = os.path.splitext(file_path)
                ext = ext.lower()
                # 확장자 목록에 '.'이 없는 경우도 처리
                normalized_extensions = [
                    e.lower() if e.startswith('.') else f'.{e.lower()}'
                    for e in file_extensions
                ]
                if ext not in normalized_extensions:
                    continue

            return rule

        return None

    def _find_rule_for_scan(self, file_path: str) -> Optional[Dict[str, Any]]:
        """수동 스캔용 규칙 매칭 - enabled 여부와 무관하게 동작"""
        if not self.rules:
            return None
        for rule in self.rules:
            folder_pattern = rule.get('folder_pattern', '')
            file_extensions = rule.get('file_extensions', [])
            if not folder_pattern:
                continue
            if folder_pattern not in file_path:
                continue
            if file_extensions:
                _, ext = os.path.splitext(file_path)
                ext = ext.lower()
                normalized_extensions = [
                    e.lower() if e.startswith('.') else f'.{e.lower()}'
                    for e in file_extensions
                ]
                if ext not in normalized_extensions:
                    continue
            return rule
        return None

    DONE_FILENAME = '.transcoding_done'

    def _load_done_list(self, directory: str) -> set:
        """디렉토리의 .transcoding_done 파일에서 이미 처리된 파일 목록을 로드"""
        done_path = os.path.join(directory, self.DONE_FILENAME)
        done_set = set()
        if os.path.exists(done_path):
            try:
                with open(done_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            done_set.add(line)
            except Exception as e:
                logging.warning(f".transcoding_done 읽기 실패 ({directory}): {e}")
        return done_set

    def _mark_done(self, directory: str, filename: str):
        """파일을 .transcoding_done에 기록"""
        done_path = os.path.join(directory, self.DONE_FILENAME)
        try:
            with open(done_path, 'a', encoding='utf-8') as f:
                f.write(filename + '\n')
        except Exception as e:
            logging.warning(f".transcoding_done 쓰기 실패 ({directory}): {e}")

    def process_folder(self, folder_path: str) -> int:
        """폴더 내 매칭되는 파일들을 트랜스코딩. 처리된 파일 수 반환."""
        if not self.enabled or not self.rules:
            return 0

        if self._processing:
            logging.warning("트랜스코딩이 이미 진행 중입니다. 건너뜁니다.")
            return 0

        self._processing = True
        processed_count = 0

        try:
            if not os.path.exists(folder_path):
                logging.warning(f"트랜스코딩 대상 폴더가 존재하지 않습니다: {folder_path}")
                return 0

            for root, dirs, files in os.walk(folder_path):
                done_set = self._load_done_list(root)
                for filename in files:
                    file_path = os.path.join(root, filename)
                    rule = self.find_matching_rule(file_path)

                    if rule is None:
                        continue

                    # 임시 파일과 추적 파일 건너뛰기
                    if filename.endswith('.tmp') or '.transcoding_tmp.' in filename:
                        continue
                    if filename == self.DONE_FILENAME:
                        continue

                    # .transcoding_done 파일로 이미 처리 여부 확인
                    if filename in done_set:
                        continue

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

    def collect_matching_files(self, folder_path: str) -> List[Dict[str, Any]]:
        """폴더에서 규칙에 매칭되는 파일 목록을 수집 (enabled 무관, 수동 스캔용)"""
        matched_files = []
        if not self.rules:
            logging.warning("트랜스코딩 규칙이 없습니다. 설정에서 규칙을 추가하세요.")
            return matched_files
        if not os.path.exists(folder_path):
            logging.warning(f"스캔 대상 경로가 존재하지 않습니다: {folder_path}")
            return matched_files

        logging.info(f"파일 수집 시작: {folder_path}")
        for root, dirs, files in os.walk(folder_path):
            done_set = self._load_done_list(root)
            for filename in files:
                file_path = os.path.join(root, filename)
                rule = self._find_rule_for_scan(file_path)

                if rule is None:
                    continue

                if filename.endswith('.tmp') or '.transcoding_tmp.' in filename:
                    continue
                if filename == self.DONE_FILENAME:
                    continue

                # .transcoding_done 파일로 이미 처리 여부 확인
                if filename in done_set:
                    logging.debug(f"이미 처리됨 (건너뜀): {file_path}")
                    continue

                # 출력 패턴 기반 건너뛰기 (보조)
                output_pattern = rule.get('output_pattern', '{{filename}}.transcoded.{{ext}}')
                if '{{filename}}' in output_pattern:
                    pattern_parts = output_pattern.replace('{{ext}}', '').replace('{{filename}}', '')
                    if pattern_parts and pattern_parts in filename:
                        continue

                matched_files.append({
                    'file_path': file_path,
                    'filename': filename,
                    'folder': root,
                    'rule': rule,
                    'rule_name': rule.get('name', '')
                })

        logging.info(f"파일 수집 완료: {len(matched_files)}개 발견")
        return matched_files

    def scan_all_folders(self, mount_path: str, progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
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
        if self._processing:
            if progress_callback:
                progress_callback({
                    'phase': 'error',
                    'message': '트랜스코딩이 이미 진행 중입니다.'
                })
            return {'completed': 0, 'failed': 0, 'total': 0}

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
                'message': '폴더 스캔 중...'
            })

            all_matched = self.collect_matching_files(mount_path)
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

        return {'completed': completed, 'failed': failed, 'total': len(all_matched)}

    def cancel_scan(self):
        """진행 중인 스캔 취소"""
        self._scan_cancel = True
