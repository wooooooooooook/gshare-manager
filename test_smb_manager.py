import unittest
from unittest.mock import MagicMock, patch, mock_open
import sys
import os

# Mock yaml module before importing config
sys.modules['yaml'] = MagicMock()

# Add app directory to path
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app'))

from smb_manager import SMBManager  # noqa: E402

class DummyConfig:
    SMB_LINKS_DIR = '/tmp/links'
    SMB_USERNAME = 'smbuser'
    SMB_PASSWORD = 'password'
    SMB_PORT = 445
    SMB_SHARE_NAME = 'gshare'
    SMB_COMMENT = 'comment'
    SMB_GUEST_OK = False
    MOUNT_PATH = '/mnt/gshare'

class TestSMBManagerRefactor(unittest.TestCase):
    def setUp(self):
        # Patch methods called in __init__
        self.patcher_init = patch('smb_manager.SMBManager._init_smb_config')
        self.patcher_ownership = patch('smb_manager.SMBManager._set_smb_user_ownership')
        self.patcher_perms = patch('smb_manager.SMBManager._set_links_directory_permissions')
        self.patcher_cleanup = patch('smb_manager.SMBManager.cleanup_all_symlinks')

        self.mock_init = self.patcher_init.start()
        self.mock_ownership = self.patcher_ownership.start()
        self.mock_perms = self.patcher_perms.start()
        self.mock_cleanup = self.patcher_cleanup.start()

        self.config = DummyConfig()
        self.smb_manager = SMBManager(self.config, 1000, 1000)

    def tearDown(self):
        self.patcher_init.stop()
        self.patcher_ownership.stop()
        self.patcher_perms.stop()
        self.patcher_cleanup.stop()

    @patch('smb_manager.pwd.getpwnam')
    def test_get_user_info_success(self, mock_getpwnam):
        # Mock successful pwd lookup
        mock_struct = MagicMock()
        mock_struct.pw_uid = 1001
        mock_struct.pw_gid = 1001
        mock_getpwnam.return_value = mock_struct

        uid, gid = self.smb_manager._get_user_info('smbuser')
        self.assertEqual(uid, 1001)
        self.assertEqual(gid, 1001)
        mock_getpwnam.assert_called_with('smbuser')

    @patch('smb_manager.pwd.getpwnam')
    def test_get_user_info_fail(self, mock_getpwnam):
        # Mock failed pwd lookup
        mock_getpwnam.side_effect = KeyError("user not found")

        result = self.smb_manager._get_user_info('nonexistent')
        self.assertIsNone(result)

    @patch('smb_manager.grp.getgrgid')
    def test_get_group_name_success(self, mock_getgrgid):
        # Mock successful grp lookup
        mock_struct = MagicMock()
        mock_struct.gr_name = "mygroup"
        mock_getgrgid.return_value = mock_struct

        group_name = self.smb_manager._get_group_name(1001)
        self.assertEqual(group_name, "mygroup")
        mock_getgrgid.assert_called_with(1001)

    @patch('smb_manager.grp.getgrgid')
    def test_get_group_name_fail(self, mock_getgrgid):
        # Mock failed grp lookup
        mock_getgrgid.side_effect = KeyError("group not found")

        result = self.smb_manager._get_group_name(9999)
        self.assertIsNone(result)

    def test_init_smb_config_calls_get_user_info(self):
        self.patcher_init.stop()
        try:
            with patch('smb_manager.subprocess.run') as mock_run, \
                 patch('smb_manager.open', mock_open()), \
                 patch.object(self.smb_manager, '_get_user_info', return_value=(1000, 1000)) as mock_get_user_info:

                mock_run.return_value.returncode = 0
                self.smb_manager._init_smb_config()
                mock_get_user_info.assert_called()
        finally:
            self.patcher_init.start()

class TestSMBManagerCache(unittest.TestCase):
    def setUp(self):
        self.patcher_init = patch('smb_manager.SMBManager._init_smb_config')
        self.patcher_ownership = patch('smb_manager.SMBManager._set_smb_user_ownership')
        self.patcher_perms = patch('smb_manager.SMBManager._set_links_directory_permissions')
        self.patcher_cleanup = patch('smb_manager.SMBManager.cleanup_all_symlinks')

        self.mock_init = self.patcher_init.start()
        self.mock_ownership = self.patcher_ownership.start()
        self.mock_perms = self.patcher_perms.start()
        self.mock_cleanup = self.patcher_cleanup.start()

        self.config = DummyConfig()
        self.smb_manager = SMBManager(self.config, 1000, 1000)

    def tearDown(self):
        self.patcher_init.stop()
        self.patcher_ownership.stop()
        self.patcher_perms.stop()
        self.patcher_cleanup.stop()

    def test_check_smb_status_requires_config_and_process(self):
        with patch.object(self.smb_manager, '_check_smb_status_from_file', return_value=True), \
             patch.object(self.smb_manager, '_check_samba_process_status', return_value=True):
            self.assertTrue(self.smb_manager.check_smb_status())

        with patch.object(self.smb_manager, '_check_smb_status_from_file', return_value=True), \
             patch.object(self.smb_manager, '_check_samba_process_status', return_value=False):
            self.assertFalse(self.smb_manager.check_smb_status())

    def test_cache_logic(self):
        # 1. Initial state
        self.assertEqual(len(self.smb_manager._active_links), 0)
        self.assertFalse(self.smb_manager.is_link_active('Folder/A'))

        # Prepare for create_symlink which now uses pwd/grp/os.lchown
        # We need to mock pwd.getpwnam and os.lchown (or chown)
        # Also since _smb_uid is None initially, it will call _refresh_ownership_ids

        mock_pw = MagicMock()
        mock_pw.pw_uid = 1001

        with patch('smb_manager.os.symlink'), \
             patch('smb_manager.os.path.lexists', return_value=False), \
             patch('smb_manager.pwd.getpwnam', return_value=mock_pw), \
             patch('smb_manager.grp.getgrgid'), \
             patch('smb_manager.os.lchown') as mock_lchown:

            # 2. Create link
            success = self.smb_manager.create_symlink('Folder/A')
            self.assertTrue(success)
            self.assertTrue(self.smb_manager.is_link_active('Folder/A'))
            self.assertIn('Folder_A', self.smb_manager._active_links)

            # Verify cached ID usage
            self.assertEqual(self.smb_manager._smb_uid, 1001)
            # Verify os.lchown called
            mock_lchown.assert_called()

        # 3. Create another link - should use cached IDs and not refresh
        with patch('smb_manager.os.symlink'), \
             patch('smb_manager.os.path.lexists', return_value=False), \
             patch('smb_manager.pwd.getpwnam') as mock_getpwnam, \
             patch('smb_manager.os.lchown') as mock_lchown:

            self.smb_manager.create_symlink('Folder/B')
            self.assertTrue(self.smb_manager.is_link_active('Folder/B'))
            self.assertEqual(len(self.smb_manager._active_links), 2)

            # Should NOT refresh IDs again
            mock_getpwnam.assert_not_called()
            mock_lchown.assert_called()

        # 4. Remove link
        with patch('smb_manager.os.remove'), \
             patch('smb_manager.os.path.exists', side_effect=lambda x: True), \
             patch('smb_manager.os.listdir', return_value=[]):
             with patch.object(self.smb_manager, 'deactivate_smb_share'):
                 success = self.smb_manager.remove_symlink('Folder/A')
                 self.assertTrue(success)
                 self.assertFalse(self.smb_manager.is_link_active('Folder/A'))
                 self.assertTrue(self.smb_manager.is_link_active('Folder/B'))
                 self.assertEqual(len(self.smb_manager._active_links), 1)

    def test_create_symlink_reuses_existing_same_target(self):
        with patch('smb_manager.os.path.islink', return_value=True), \
             patch('smb_manager.os.readlink', return_value='/mnt/gshare/Folder/A'), \
             patch('smb_manager.os.path.lexists') as mock_lexists, \
             patch('smb_manager.os.symlink') as mock_symlink:
            success = self.smb_manager.create_symlink('Folder/A')

        self.assertTrue(success)
        self.assertIn('Folder_A', self.smb_manager._active_links)
        mock_symlink.assert_not_called()

    def test_create_symlink_file_exists_with_same_target_treated_as_success(self):
        mock_pw = MagicMock()
        mock_pw.pw_uid = 1001

        with patch('smb_manager.os.path.islink', side_effect=[False, True]), \
             patch('smb_manager.os.path.lexists', return_value=False), \
             patch('smb_manager.os.symlink', side_effect=FileExistsError), \
             patch('smb_manager.os.readlink', return_value='/mnt/gshare/Folder/A'), \
             patch('smb_manager.pwd.getpwnam', return_value=mock_pw), \
             patch('smb_manager.grp.getgrgid'), \
             patch('smb_manager.os.lchown'):
            success = self.smb_manager.create_symlink('Folder/A')

        self.assertTrue(success)
        self.assertIn('Folder_A', self.smb_manager._active_links)

class TestSMBManagerCleanup(unittest.TestCase):
    def setUp(self):
        self.patcher_init = patch('smb_manager.SMBManager._init_smb_config')
        self.patcher_ownership = patch('smb_manager.SMBManager._set_smb_user_ownership')
        self.patcher_perms = patch('smb_manager.SMBManager._set_links_directory_permissions')

        self.mock_init = self.patcher_init.start()
        self.mock_ownership = self.patcher_ownership.start()
        self.mock_perms = self.patcher_perms.start()

        self.config = DummyConfig()

        with patch('smb_manager.SMBManager.cleanup_all_symlinks'):
            self.smb_manager = SMBManager(self.config, 1000, 1000)

    def tearDown(self):
        self.patcher_init.stop()
        self.patcher_ownership.stop()
        self.patcher_perms.stop()

    def test_cleanup_real_method(self):
        self.smb_manager._active_links.add('A_B')

        with patch('smb_manager.os.path.exists', return_value=True), \
             patch('smb_manager.os.listdir', return_value=['A_B']), \
             patch('smb_manager.os.path.islink', return_value=True), \
             patch('smb_manager.os.remove') as mock_remove:

            self.smb_manager.cleanup_all_symlinks()
            self.assertEqual(len(self.smb_manager._active_links), 0)
            mock_remove.assert_called()

if __name__ == '__main__':
    unittest.main()
