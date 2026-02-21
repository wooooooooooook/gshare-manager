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
from main import FolderMonitor  # noqa: E402


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
    def test_list_subfolders_find_command_uses_escaped_null_delimiter(self, _mock_ownership, _mock_smb):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, 'alpha'))

            monitor = FolderMonitor(DummyConfig(root), MagicMock(), 0.0)

            with patch('main.subprocess.run', return_value=MagicMock(stdout='alpha\0')) as mock_run:
                result = monitor._list_subfolders_without_mtime()

            self.assertEqual(result, ['alpha'])
            cmd = mock_run.call_args.args[0]
            self.assertIn(r'%P\0', cmd)
            self.assertNotIn('%P\x00', cmd)


if __name__ == '__main__':
    unittest.main()
