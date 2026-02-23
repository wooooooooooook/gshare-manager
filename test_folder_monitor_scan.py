import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Mock external modules before importing main
sys.modules['proxmox_api'] = MagicMock()
sys.modules['mqtt_manager'] = MagicMock()
sys.modules['web_server'] = MagicMock()
sys.modules['transcoder'] = MagicMock()

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app'))
from main import FolderMonitor, GShareManager, _is_nfs_mount_present  # noqa: E402


class DummyConfig:
    def __init__(self, mount_path):
        self.MOUNT_PATH = mount_path
        self.SMB_LINKS_DIR = '/tmp/links'
        self.TIMEZONE = 'UTC'


class TestFolderMonitorFindFallback(unittest.TestCase):
    @patch('main.SMBManager')
    @patch('main.FolderMonitor._get_nfs_ownership', return_value=(1000, 1000))
    def test_scan_folders_hybrid_falls_back_to_portable_find(self, _mock_ownership, _mock_smb):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, 'A'))
            os.makedirs(os.path.join(root, 'B', 'C'))
            with open(os.path.join(root, 'A', 'a.txt'), 'w', encoding='utf-8') as f:
                f.write('x')
            with open(os.path.join(root, 'B', 'C', 'c.txt'), 'w', encoding='utf-8') as f:
                f.write('x')

            monitor = FolderMonitor(DummyConfig(root), MagicMock(), 0.0)

            first_error = __import__('subprocess').CalledProcessError(1, ['find'])
            second_ok = MagicMock(stdout='./A/a.txt\0./B/C/c.txt\0')

            with patch('main.subprocess.run', side_effect=[first_error, second_ok]) as mock_run:
                result = monitor._scan_folders_hybrid()

            self.assertIn('A', result)
            self.assertIn('B/C', result)
            self.assertEqual(mock_run.call_count, 2)


class TestFolderMonitorListSubfolders(unittest.TestCase):
    @patch('main.SMBManager')
    @patch('main.FolderMonitor._get_nfs_ownership', return_value=(1000, 1000))
    def test_list_subfolders_uses_scandir_and_filters_hidden_dirs(self, _mock_ownership, _mock_smb):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, 'alpha'))
            os.makedirs(os.path.join(root, 'alpha', 'beta'))
            os.makedirs(os.path.join(root, '.hidden'))
            os.makedirs(os.path.join(root, '@eaDir'))

            monitor = FolderMonitor(DummyConfig(root), MagicMock(), 0.0)
            result = monitor._list_subfolders_without_mtime()

            self.assertEqual(result, ['alpha', 'alpha/beta'])

    @patch('main.SMBManager')
    @patch('main.FolderMonitor._get_nfs_ownership', return_value=(1000, 1000))
    def test_list_subfolders_prefers_ls_tree_when_available(self, _mock_ownership, _mock_smb):
        with tempfile.TemporaryDirectory() as root:
            monitor = FolderMonitor(DummyConfig(root), MagicMock(), 0.0)
            ls_output = '.:\nalpha/\nbeta/\n\n./alpha:\nchild/\n\n./beta:\n\n./alpha/child:\n'

            with patch('main.subprocess.run', return_value=MagicMock(stdout=ls_output)) as mock_run:
                result = monitor._list_subfolders_without_mtime()

            self.assertEqual(result, ['alpha', 'alpha/child', 'beta'])
            args, kwargs = mock_run.call_args
            self.assertEqual(args[0], ['ls', '-1RF'])
            self.assertEqual(kwargs.get('cwd'), root)


class DummyManagerConfig:
    def __init__(self, monitor_mode: str = 'event'):
        self.MONITOR_MODE = monitor_mode
        self.TIMEZONE = 'UTC'
        self.CPU_THRESHOLD = 10
        self.THRESHOLD_COUNT = 3
        self.CHECK_INTERVAL = 60
        self.NFS_PATH = ''
        self.MOUNT_PATH = '/tmp'


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



class TestNfsMountPresence(unittest.TestCase):
    def test_is_nfs_mount_present_parses_proc_mounts(self):
        mounts_data = """server:/data /mnt/test nfs4 rw,relatime 0 0\n"""

        with patch('main.os.path.realpath', side_effect=lambda p: p):
            with patch('builtins.open', unittest.mock.mock_open(read_data=mounts_data)):
                self.assertTrue(_is_nfs_mount_present('/mnt/test', 'server:/data'))
                self.assertFalse(_is_nfs_mount_present('/mnt/test', 'server:/other'))


if __name__ == '__main__':
    unittest.main()
