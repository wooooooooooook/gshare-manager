import os
import unittest
from unittest.mock import MagicMock, patch
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from main import FolderMonitor, GShareManager
from config import GshareConfig


class TestNFSRecovery(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock(spec=GshareConfig)
        self.config.TIMEZONE = "Asia/Seoul"
        self.config.NFS_PATH = "192.168.0.8:/volume2/homes/woooook/Photos"
        self.config.MOUNT_PATH = "/tmp/test_nfs_mount"
        self.config.SMB_LINKS_DIR = "/tmp/test_links"
        self.config.SMB_USERNAME = "nobody"
        self.config.SMB_PASSWORD = ""
        self.config.SMB_SHARE_NAME = "gshare"
        self.config.NFS_MOUNT_ENABLED = True
        self.config.CHECK_INTERVAL = 10
        self.config.GSHARE_ENABLED = True
        self.config.VM_MONITOR_ENABLED = False
        self.mock_proxmox = MagicMock()

    @patch("main.SMBManager")
    @patch("main._is_nfs_mount_present", return_value=True)
    @patch("os.stat")
    @patch("os.listdir")
    def test_check_nfs_status_healthy(self, mock_listdir, mock_stat, mock_present, mock_smb):
        monitor = FolderMonitor(self.config, self.mock_proxmox, 0.0)
        self.assertTrue(monitor.check_nfs_status())

    @patch("main.SMBManager")
    @patch("main._is_nfs_mount_present", return_value=True)
    @patch("os.stat", side_effect=OSError(116, "Stale file handle"))
    def test_check_nfs_status_stale(self, mock_stat, mock_present, mock_smb):
        monitor = FolderMonitor(self.config, self.mock_proxmox, 0.0)
        self.assertFalse(monitor.check_nfs_status())

    @patch("main.SMBManager")
    @patch("main._is_nfs_mount_present", return_value=True)
    @patch("os.stat", side_effect=OSError(116, "Stale file handle"))
    @patch("os.path.exists", return_value=True)
    @patch("subprocess.run")
    def test_mount_nfs_recovers_from_stale(self, mock_subproc, mock_exists, mock_stat, mock_present, mock_smb):
        manager = GShareManager(self.config, self.mock_proxmox)
        mock_subproc.return_value = MagicMock(returncode=0)

        manager._mount_nfs()

        # umount -f -l should have been called first
        calls = [c[0][0] for c in mock_subproc.call_args_list]
        self.assertTrue(any("umount" in cmd and "-l" in cmd for cmd in calls))
        # Then mount command should have been called
        self.assertTrue(any("mount" in cmd and "-t" in cmd for cmd in calls))


if __name__ == '__main__':
    unittest.main()
