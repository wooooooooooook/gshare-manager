import os
import sys
import unittest
import tempfile
import shutil
import pytz
from datetime import datetime
from unittest.mock import MagicMock, patch

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app'))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Windows test compatibility for symlinks
_virtual_links: dict[str, str] = {}
_orig_symlink = os.symlink
_orig_islink = os.path.islink
_orig_readlink = getattr(os, 'readlink', None)
_orig_lexists = os.path.lexists
_orig_replace = os.replace


def _mock_symlink(src, dst, target_is_directory=False):
    try:
        _orig_symlink(src, dst, target_is_directory=target_is_directory)
    except OSError:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'w', encoding='utf-8') as f:
            f.write(f"SYMLINK:{src}")
        _virtual_links[os.path.abspath(dst)] = src


def _mock_islink(path):
    if os.path.abspath(path) in _virtual_links:
        return True
    return _orig_islink(path)


def _mock_readlink(path):
    p = os.path.abspath(path)
    if p in _virtual_links:
        return _virtual_links[p]
    if _orig_readlink:
        return _orig_readlink(path)
    raise OSError(f"Cannot readlink {path}")


def _mock_lexists(path):
    if os.path.abspath(path) in _virtual_links:
        return True
    return _orig_lexists(path)


def _mock_replace(src, dst):
    src_abs = os.path.abspath(src)
    dst_abs = os.path.abspath(dst)
    if src_abs in _virtual_links:
        _virtual_links[dst_abs] = _virtual_links.pop(src_abs)
    _orig_replace(src, dst)


from main import RecentFileTracker, FolderMonitor, GShareManager, State
from smb_manager import SMBManager
from config import GshareConfig


class TestRecentFileTracker(unittest.TestCase):
    def setUp(self):
        self.tz = pytz.timezone('Asia/Seoul')
        self.tracker = RecentFileTracker(tz=self.tz, max_days=3)

    def test_case_1_today_new_file_event(self):
        """1. 오늘 신규 파일 이벤트 -> 오늘 set에 추가"""
        self.tracker.add_file_event('/mnt/gshare/folder/file1.txt', event_date_str='2026-09-16')
        daily_files = self.tracker.get_daily_files()
        
        self.assertIn('2026-09-16', daily_files)
        self.assertEqual(daily_files['2026-09-16'], {'/mnt/gshare/folder/file1.txt'})
        self.assertEqual(self.tracker.get_recent_files('2026-09-16'), {'/mnt/gshare/folder/file1.txt'})

    def test_case_2_duplicate_file_events(self):
        """2. 동일 파일 이벤트 반복 -> 중복 없음"""
        self.tracker.add_file_event('/mnt/gshare/folder/file1.txt', event_date_str='2026-09-16')
        self.tracker.add_file_event('/mnt/gshare/folder/file1.txt', event_date_str='2026-09-16')
        self.tracker.add_file_event('/mnt/gshare/folder/file1.txt', event_date_str='2026-09-16')
        
        daily_files = self.tracker.get_daily_files()
        self.assertEqual(len(daily_files['2026-09-16']), 1)
        self.assertEqual(self.tracker.get_recent_files('2026-09-16'), {'/mnt/gshare/folder/file1.txt'})

    def test_case_3_multiple_different_files(self):
        """3. 서로 다른 파일 여러 개 -> 모두 저장"""
        self.tracker.add_file_event('/mnt/gshare/folder/file1.txt', event_date_str='2026-09-16')
        self.tracker.add_file_event('/mnt/gshare/folder/file2.pdf', event_date_str='2026-09-16')
        self.tracker.add_file_event('/mnt/gshare/other/file3.jpg', event_date_str='2026-09-16')
        
        expected = {
            '/mnt/gshare/folder/file1.txt',
            '/mnt/gshare/folder/file2.pdf',
            '/mnt/gshare/other/file3.jpg'
        }
        self.assertEqual(self.tracker.get_daily_files()['2026-09-16'], expected)
        self.assertEqual(self.tracker.get_recent_files('2026-09-16'), expected)

    def test_case_4_and_5_rolling_window_and_pruning(self):
        """4. 날짜 변경 -> 3일 rolling window 정상 이동 & 5. 3일 이전 날짜 -> 자동 제거"""
        # Day 1: 09-14
        self.tracker.add_file_event('/mnt/gshare/a.txt', event_date_str='2026-09-14')
        self.tracker.add_file_event('/mnt/gshare/b.pdf', event_date_str='2026-09-14')
        
        # Day 2: 09-15
        self.tracker.add_file_event('/mnt/gshare/c.jpg', event_date_str='2026-09-15')
        
        # Day 3: 09-16
        self.tracker.add_file_event('/mnt/gshare/d.pdf', event_date_str='2026-09-16')
        
        # At 09-16: window is [09-14, 09-15, 09-16]
        recent_0916 = self.tracker.get_recent_files('2026-09-16')
        self.assertEqual(recent_0916, {
            '/mnt/gshare/a.txt',
            '/mnt/gshare/b.pdf',
            '/mnt/gshare/c.jpg',
            '/mnt/gshare/d.pdf'
        })
        self.assertIn('2026-09-14', self.tracker.get_daily_files())

        # Day 4: 09-17 (Day 1: 09-14 should be pruned!)
        self.tracker.add_file_event('/mnt/gshare/e.png', event_date_str='2026-09-17')
        daily_0917 = self.tracker.get_daily_files()
        self.assertNotIn('2026-09-14', daily_0917)
        self.assertIn('2026-09-15', daily_0917)
        self.assertIn('2026-09-16', daily_0917)
        self.assertIn('2026-09-17', daily_0917)
        
        recent_0917 = self.tracker.get_recent_files('2026-09-17')
        self.assertEqual(recent_0917, {
            '/mnt/gshare/c.jpg',
            '/mnt/gshare/d.pdf',
            '/mnt/gshare/e.png'
        })

        # Day 5: 09-18 (Day 2: 09-15 should be pruned!)
        recent_0918 = self.tracker.get_recent_files('2026-09-18')
        daily_0918 = self.tracker.get_daily_files()
        self.assertNotIn('2026-09-15', daily_0918)
        self.assertEqual(recent_0918, {
            '/mnt/gshare/d.pdf',
            '/mnt/gshare/e.png'
        })

    def test_case_6_same_file_on_different_dates(self):
        """6. 동일 파일이 서로 다른 날짜에 이벤트 발생 -> 최종 SMB 집합에는 한 번만 존재"""
        # 09-14: a.txt, b.pdf
        self.tracker.add_file_event('/mnt/gshare/a.txt', event_date_str='2026-09-14')
        self.tracker.add_file_event('/mnt/gshare/b.pdf', event_date_str='2026-09-14')
        
        # 09-15: c.jpg
        self.tracker.add_file_event('/mnt/gshare/c.jpg', event_date_str='2026-09-15')
        
        # 09-16: a.txt, d.pdf
        self.tracker.add_file_event('/mnt/gshare/a.txt', event_date_str='2026-09-16')
        self.tracker.add_file_event('/mnt/gshare/d.pdf', event_date_str='2026-09-16')
        
        recent = self.tracker.get_recent_files('2026-09-16')
        self.assertEqual(recent, {
            '/mnt/gshare/a.txt',
            '/mnt/gshare/b.pdf',
            '/mnt/gshare/c.jpg',
            '/mnt/gshare/d.pdf'
        })
        self.assertEqual(len(recent), 4)

        # On 09-17: 09-14 is pruned, but a.txt was touched on 09-16 so it remains
        recent_0917 = self.tracker.get_recent_files('2026-09-17')
        self.assertEqual(recent_0917, {
            '/mnt/gshare/a.txt',
            '/mnt/gshare/c.jpg',
            '/mnt/gshare/d.pdf'
        })

    def test_case_9_process_restart_memory_reset(self):
        """9. 프로세스 재시작 -> 메모리 목록 초기화됨을 전제로 정상 동작"""
        self.tracker.add_file_event('/mnt/gshare/a.txt', event_date_str='2026-09-16')
        self.assertEqual(len(self.tracker.get_recent_files('2026-09-16')), 1)
        
        new_tracker = RecentFileTracker(tz=self.tz, max_days=3)
        self.assertEqual(len(new_tracker.get_recent_files('2026-09-16')), 0)
        self.assertEqual(new_tracker.get_daily_files(), {})


class TestSMBSyncIntegration(unittest.TestCase):
    def setUp(self):
        _virtual_links.clear()
        self.temp_dir = tempfile.mkdtemp()
        self.mount_path = os.path.join(self.temp_dir, 'mount')
        self.links_dir = os.path.join(self.temp_dir, 'links')
        os.makedirs(self.mount_path, exist_ok=True)
        os.makedirs(self.links_dir, exist_ok=True)

        self.config = MagicMock(spec=GshareConfig)
        self.config.MOUNT_PATH = self.mount_path
        self.config.SMB_LINKS_DIR = self.links_dir
        self.config.SMB_USERNAME = 'nobody'
        self.config.SMB_PASSWORD = ''
        self.config.SMB_SHARE_NAME = 'gshare'
        self.config.SMB_COMMENT = 'test share'
        self.config.SMB_GUEST_OK = True
        self.config.SMB_PORT = 445
        self.config.SMB_SHARE_MODE = 'file'
        self.config.TIMEZONE = 'Asia/Seoul'

        self.patches = [
            patch('os.symlink', side_effect=_mock_symlink),
            patch('os.path.islink', side_effect=_mock_islink),
            patch('os.readlink', side_effect=_mock_readlink),
            patch('os.path.lexists', side_effect=_mock_lexists),
            patch('os.replace', side_effect=_mock_replace),
            patch.object(SMBManager, '_init_smb_config', return_value=None),
            patch.object(SMBManager, '_set_smb_user_ownership', return_value=None),
            patch.object(SMBManager, '_set_links_directory_permissions', return_value=None),
            patch.object(SMBManager, 'check_smb_status', return_value=True),
            patch.object(SMBManager, 'activate_smb_share', return_value=True),
            patch.object(SMBManager, 'deactivate_smb_share', return_value=True),
            patch.object(SMBManager, '_check_samba_process_status', return_value=True),
            patch.object(SMBManager, '_check_smb_status_from_file', return_value=True),
            patch.object(SMBManager, '_apply_symlink_ownership', return_value=None),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

        self.smb_manager = SMBManager(self.config, nfs_uid=1000, nfs_gid=1000)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_case_7_missing_file_skipped_without_affecting_others(self):
        """7. 존재하지 않는 파일 하나가 포함되어도 다른 파일 SMB 공유에는 영향 없음"""
        real_file = os.path.join(self.mount_path, 'real.txt')
        with open(real_file, 'w') as f:
            f.write('content')
        
        non_existent_file = os.path.join(self.mount_path, 'missing.txt')
        
        files = {real_file, non_existent_file}
        success_count, fail_count = self.smb_manager.sync_file_symlinks(files)
        
        self.assertEqual(success_count, 1)
        self.assertEqual(fail_count, 1)
        
        real_link = os.path.join(self.links_dir, 'real.txt')
        self.assertTrue(os.path.islink(real_link))
        self.assertEqual(os.path.normpath(os.readlink(real_link)), os.path.normpath(real_file))
        
        missing_link = os.path.join(self.links_dir, 'missing.txt')
        self.assertFalse(os.path.exists(missing_link))

    def test_case_8_smb_sync_updates_to_recent_3_days_set(self):
        """8. 이벤트가 들어올 때 SMB 공유 대상이 최근 3일 전체 파일 집합으로 갱신됨"""
        file1 = os.path.join(self.mount_path, 'file1.txt')
        file2 = os.path.join(self.mount_path, 'file2.txt')
        file3 = os.path.join(self.mount_path, 'file3.txt')
        for f in [file1, file2, file3]:
            with open(f, 'w') as fp:
                fp.write('data')

        # Step 1: files in set: file1, file2
        self.smb_manager.sync_file_symlinks({file1, file2})
        self.assertTrue(os.path.islink(os.path.join(self.links_dir, 'file1.txt')))
        self.assertTrue(os.path.islink(os.path.join(self.links_dir, 'file2.txt')))
        self.assertFalse(os.path.exists(os.path.join(self.links_dir, 'file3.txt')))

        # Step 2: files in set: file2, file3 (file1 expired)
        self.smb_manager.sync_file_symlinks({file2, file3})
        self.assertFalse(os.path.exists(os.path.join(self.links_dir, 'file1.txt')))
        self.assertTrue(os.path.islink(os.path.join(self.links_dir, 'file2.txt')))
        self.assertTrue(os.path.islink(os.path.join(self.links_dir, 'file3.txt')))


class TestGShareManagerEventHandling(unittest.TestCase):
    def setUp(self):
        _virtual_links.clear()
        self.temp_dir = tempfile.mkdtemp()
        self.mount_path = os.path.join(self.temp_dir, 'mount')
        self.links_dir = os.path.join(self.temp_dir, 'links')
        os.makedirs(self.mount_path, exist_ok=True)
        os.makedirs(self.links_dir, exist_ok=True)

        self.config = MagicMock(spec=GshareConfig)
        self.config.MOUNT_PATH = self.mount_path
        self.config.SMB_LINKS_DIR = self.links_dir
        self.config.SMB_USERNAME = 'nobody'
        self.config.SMB_PASSWORD = ''
        self.config.SMB_SHARE_NAME = 'gshare'
        self.config.SMB_COMMENT = 'test share'
        self.config.SMB_GUEST_OK = True
        self.config.SMB_PORT = 445
        self.config.SMB_SHARE_MODE = 'file'
        self.config.MONITOR_MODE = 'event'
        self.config.EVENT_ENABLED = True
        self.config.NFS_MOUNT_ENABLED = False
        self.config.TIMEZONE = 'Asia/Seoul'
        self.config.CPU_THRESHOLD = 10.0
        self.config.THRESHOLD_COUNT = 3
        self.config.CHECK_INTERVAL = 60
        self.config.GSHARE_ENABLED = True
        self.config.MQTT_ENABLED = False
        self.config.POLLING_ENABLED = False
        self.config.SMB_ENABLED = True
        self.config.VM_MONITOR_ENABLED = False
        self.config.TRANSCODING_ENABLED = False
        self.config.TRANSCODING_RULES = []
        self.config.TRANSCODING_DONE_FILENAME = '.transcode_done'

        self.proxmox_api = MagicMock()
        self.proxmox_api.is_vm_running.return_value = True

        self.patches = [
            patch('os.symlink', side_effect=_mock_symlink),
            patch('os.path.islink', side_effect=_mock_islink),
            patch('os.readlink', side_effect=_mock_readlink),
            patch('os.path.lexists', side_effect=_mock_lexists),
            patch('os.replace', side_effect=_mock_replace),
            patch.object(SMBManager, '_init_smb_config', return_value=None),
            patch.object(SMBManager, '_set_smb_user_ownership', return_value=None),
            patch.object(SMBManager, '_set_links_directory_permissions', return_value=None),
            patch.object(SMBManager, 'check_smb_status', return_value=True),
            patch.object(SMBManager, 'activate_smb_share', return_value=True),
            patch.object(SMBManager, 'deactivate_smb_share', return_value=True),
            patch.object(SMBManager, '_check_samba_process_status', return_value=True),
            patch.object(SMBManager, '_check_smb_status_from_file', return_value=True),
            patch.object(SMBManager, '_apply_symlink_ownership', return_value=None),
            patch.object(GShareManager, '_load_last_shutdown_time', return_value=0.0),
            patch.object(GShareManager, 'update_state', return_value=MagicMock()),
            patch.object(FolderMonitor, '_load_scan_cache', return_value=False),
            patch.object(FolderMonitor, '_save_scan_cache', return_value=None),
            patch.object(FolderMonitor, '_get_nfs_ownership', return_value=(1000, 1000)),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

        self.manager = GShareManager(self.config, self.proxmox_api)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_case_10_existing_nfs_event_handling_preserved(self):
        """10. 기존 NFS event 수신 및 기존 이벤트 처리 기능이 깨지지 않음"""
        subfolder = os.path.join(self.mount_path, 'Sub1')
        os.makedirs(subfolder, exist_ok=True)
        file_path = os.path.join(subfolder, 'sample.txt')
        with open(file_path, 'w') as f:
            f.write('hello')

        ok, detail = self.manager.handle_folder_event('Sub1', 'sample.txt')
        self.assertTrue(ok)
        self.assertIn('sample.txt', detail)

        self.assertIn('Sub1', self.manager.folder_monitor.previous_mtimes)
        self.assertIsNotNone(self.manager.last_file_event_time)
        recent = self.manager.get_recent_files()
        normalized_full = os.path.normpath(file_path).replace('\\', '/')
        self.assertIn(normalized_full, recent)
        symlink_path = os.path.join(self.links_dir, 'sample.txt')
        self.assertTrue(os.path.islink(symlink_path))


if __name__ == '__main__':
    unittest.main()
