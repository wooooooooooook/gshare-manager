import os
import sys
import tempfile
import unittest
import json
from unittest.mock import MagicMock, patch

# Mock external modules before importing main
sys.modules['proxmox_api'] = MagicMock()
sys.modules['mqtt_manager'] = MagicMock()
sys.modules['web_server'] = MagicMock()
sys.modules['transcoder'] = MagicMock()
sys.modules['requests'] = MagicMock()
sys.modules['yaml'] = MagicMock()
from datetime import timezone, timedelta
from unittest.mock import MagicMock
import sys

# Mock pytz properly
real_timezone = timezone(timedelta(hours=9))
sys.modules['pytz'] = MagicMock()
sys.modules['pytz'].timezone.return_value = real_timezone
sys.modules['pytz'].UTC = timezone.utc

sys.modules['flask'] = MagicMock()
sys.modules['flask_socketio'] = MagicMock()
sys.modules['urllib3'] = MagicMock()

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app'))
from main import FolderMonitor, GShareManager, _is_nfs_mount_present  # noqa: E402


class DummyConfig:
    def __init__(self, mount_path):
        self.MOUNT_PATH = mount_path
        self.SMB_LINKS_DIR = '/tmp/links'
        self.TIMEZONE = 'UTC'


class TestFolderMonitorScan(unittest.TestCase):
    @patch('main.SMBManager')
    @patch('main.FolderMonitor._get_nfs_ownership', return_value=(1000, 1000))
    def test_scan_folders_parses_find_output(self, _mock_ownership, _mock_smb):
        with tempfile.TemporaryDirectory() as root:
            cfg = DummyConfig(root)
            monitor = FolderMonitor(cfg, MagicMock(), 0.0)

            find_output = '100.0\tA\n200.0\tB/C\n'
            with patch('main.subprocess.run', return_value=MagicMock(stdout=find_output)):
                result = monitor._scan_folders(full_scan=True)

            self.assertEqual(result, {'A': 100.0, 'B/C': 200.0})

    @patch('main.SMBManager')
    @patch('main.FolderMonitor._get_nfs_ownership', return_value=(1000, 1000))
    def test_scan_folders_filters_old_entries_on_partial_scan(self, _mock_ownership, _mock_smb):
        with tempfile.TemporaryDirectory() as root:
            cfg = DummyConfig(root)
            monitor = FolderMonitor(cfg, MagicMock(), 0.0)

            now = 1_000.0
            monitor._polling_recent_window_mmin = MagicMock(return_value=5)
            find_output = '990.0\tnew-folder\n600.0\told-folder\n'

            with patch('main.time.time', return_value=now), \
                 patch('main.subprocess.run', return_value=MagicMock(stdout=find_output)):
                result = monitor._scan_folders(full_scan=False)

            self.assertEqual(result, {'new-folder': 990.0})


class TestFolderScanCacheReuse(unittest.TestCase):
    @patch('main.SMBManager')
    @patch('main.FolderMonitor._get_nfs_ownership', return_value=(1000, 1000))
    def test_load_scan_cache_when_identity_matches(self, _mock_ownership, _mock_smb):
        with tempfile.TemporaryDirectory() as root:
            cache_path = os.path.join(root, '.folder_scan_cache.json')
            payload = {
                'identity': {
                    'mount_path': root.rstrip('/'),
                    'nfs_path': 'nas:/export',
                },
                'previous_mtimes': {'folder1': 123.4}
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f)

            cfg = DummyConfig(root)
            cfg.NFS_PATH = 'nas:/export'
            cfg.MONITOR_MODE = 'event'

            with patch('main.FOLDER_SCAN_CACHE_PATH', cache_path):
                monitor = FolderMonitor(cfg, MagicMock(), 0.0)

            self.assertTrue(monitor.loaded_from_cache)
            self.assertIn('folder1', monitor.previous_mtimes)

    @patch('main.SMBManager')
    @patch('main.FolderMonitor._get_nfs_ownership', return_value=(1000, 1000))
    def test_skip_scan_cache_when_identity_differs(self, _mock_ownership, _mock_smb):
        with tempfile.TemporaryDirectory() as root:
            cache_path = os.path.join(root, '.folder_scan_cache.json')
            payload = {
                'identity': {
                    'mount_path': '/other/mount',
                    'nfs_path': 'nas:/export',
                },
                'previous_mtimes': {'folder1': 123.4}
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f)

            cfg = DummyConfig(root)
            cfg.NFS_PATH = 'nas:/export'
            cfg.MONITOR_MODE = 'event'

            with patch('main.FOLDER_SCAN_CACHE_PATH', cache_path):
                monitor = FolderMonitor(cfg, MagicMock(), 0.0)

            self.assertFalse(monitor.loaded_from_cache)
            self.assertEqual(monitor.previous_mtimes, {})


class DummyManagerConfig:
    def __init__(self, monitor_mode: str = 'event'):
        self.MONITOR_MODE = monitor_mode
        self.TIMEZONE = 'UTC'
        self.CPU_THRESHOLD = 10
        self.THRESHOLD_COUNT = 3
        self.CHECK_INTERVAL = 60
        self.NFS_PATH = ''
        self.MOUNT_PATH = '/tmp'
        self.NFS_MOUNT_ENABLED = True
        self.EVENT_ENABLED = True
        self.SMB_ENABLED = True
        self.VM_MONITOR_ENABLED = True
        self.POLLING_ENABLED = True
        self.GSHARE_ENABLED = True


class TestGShareManagerInitialize(unittest.TestCase):
    @patch('main.Transcoder')
    @patch('main.GShareManager.update_state', return_value=MagicMock())
    @patch('main.GShareManager._mount_nfs', return_value=None)
    @patch('main.GShareManager._load_last_shutdown_time', return_value=0.0)
    @patch('main.FolderMonitor')
    @patch('main.threading.Thread')
    def test_initialize_starts_initial_scan_thread_even_in_event_mode(
        self,
        mock_thread,
        mock_folder_monitor,
        _mock_shutdown,
        _mock_mount,
        _mock_update_state,
        _mock_transcoder
    ):
        thread_instance = MagicMock()
        mock_thread.return_value = thread_instance

        manager = GShareManager(DummyManagerConfig('event'), MagicMock())
        manager.initialize()

        self.assertTrue(manager.initial_scan_in_progress)
        mock_thread.assert_called_once_with(target=manager._run_initial_scan_async, daemon=True)
        thread_instance.start.assert_called_once()



class TestEventHandlingCache(unittest.TestCase):
    @patch('main.Transcoder')
    @patch('main.GShareManager.update_state', return_value=MagicMock())
    @patch('main.GShareManager._mount_nfs', return_value=None)
    @patch('main.GShareManager._load_last_shutdown_time', return_value=0.0)
    @patch('main.FolderMonitor')
    def test_handle_folder_event_saves_event_time_to_cache(
        self,
        mock_folder_monitor,
        _mock_shutdown,
        _mock_mount,
        _mock_update_state,
        _mock_transcoder
    ):
        monitor_instance = MagicMock()
        monitor_instance.previous_mtimes = {}
        monitor_instance._filter_mount_targets.return_value = ['A']
        mock_folder_monitor.return_value = monitor_instance

        manager = GShareManager(DummyManagerConfig('event'), MagicMock())
        manager.smb_manager.create_symlink = MagicMock()
        manager.smb_manager.check_smb_status = MagicMock(return_value=True)
        manager.proxmox_api.is_vm_running = MagicMock(return_value=True)

        with patch('main.time.time', return_value=1234.5):
            ok, _ = manager.handle_folder_event('A')

        self.assertTrue(ok)
        self.assertEqual(manager.folder_monitor.previous_mtimes.get('A'), 1234.5)
        manager.folder_monitor._save_scan_cache.assert_called_once()


class TestNfsMountPresence(unittest.TestCase):
    def test_is_nfs_mount_present_parses_proc_mounts(self):
        mounts_data = """server:/data /mnt/test nfs4 rw,relatime 0 0\n"""

        with patch('main.os.path.realpath', side_effect=lambda p: p):
            with patch('builtins.open', unittest.mock.mock_open(read_data=mounts_data)):
                self.assertTrue(_is_nfs_mount_present('/mnt/test', 'server:/data'))
                self.assertFalse(_is_nfs_mount_present('/mnt/test', 'server:/other'))


if __name__ == '__main__':
    unittest.main()
