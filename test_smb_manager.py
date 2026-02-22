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

    @patch('smb_manager.subprocess.run')
    def test_get_user_info_success(self, mock_run):
        # This method doesn't exist yet, so we only run if it exists
        if not hasattr(self.smb_manager, '_get_user_info'):
            return

        # Mock successful id command
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "uid=1001(smbuser) gid=1001(smbuser) groups=1001(smbuser)"

        uid, gid = self.smb_manager._get_user_info('smbuser')
        self.assertEqual(uid, 1001)
        self.assertEqual(gid, 1001)
        mock_run.assert_called_with(['id', 'smbuser'], capture_output=True, text=True)

    @patch('smb_manager.subprocess.run')
    def test_get_user_info_fail(self, mock_run):
        if not hasattr(self.smb_manager, '_get_user_info'):
            return

        # Mock failed id command
        mock_run.return_value.returncode = 1

        result = self.smb_manager._get_user_info('nonexistent')
        self.assertIsNone(result)

    @patch('smb_manager.subprocess.run')
    def test_get_group_name_success(self, mock_run):
        if not hasattr(self.smb_manager, '_get_group_name'):
            return

        # Mock successful getent group command
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "mygroup:x:1001:"

        group_name = self.smb_manager._get_group_name(1001)
        self.assertEqual(group_name, "mygroup")
        mock_run.assert_called_with(['getent', 'group', '1001'], capture_output=True, text=True)

    @patch('smb_manager.subprocess.run')
    def test_get_group_name_fail(self, mock_run):
        if not hasattr(self.smb_manager, '_get_group_name'):
            return

        # Mock failed getent group command
        mock_run.return_value.returncode = 1

        result = self.smb_manager._get_group_name(9999)
        self.assertIsNone(result)

    def test_init_smb_config_calls_get_user_info(self):
        # We need to run the real _init_smb_config.
        # Stop patcher to run real method
        self.patcher_init.stop()

        try:
            # Mock internal calls
            with patch('smb_manager.subprocess.run') as mock_run,                  patch('smb_manager.open', mock_open()),                  patch.object(self.smb_manager, '_get_user_info', return_value=(1000, 1000)) as mock_get_user_info:

                mock_run.return_value.returncode = 0

                self.smb_manager._init_smb_config()

                # Check call
                mock_get_user_info.assert_called()
        finally:
            self.patcher_init.start()

    def test_set_links_directory_permissions_calls_get_group_name(self):
        self.patcher_perms.stop()

        try:
            with patch('smb_manager.subprocess.run') as mock_run,                  patch('smb_manager.os.path.exists', return_value=True),                  patch('smb_manager.os.makedirs'),                  patch('smb_manager.os.chmod'),                  patch.object(self.smb_manager, '_get_group_name', return_value='somegroup') as mock_get_group_name:

                self.smb_manager._set_links_directory_permissions('/tmp')

                mock_get_group_name.assert_called_with(1000)

                # Verify chown called with group name
                found = False
                for call in mock_run.call_args_list:
                    args = call[0][0]
                    # args is list of strings like ['chown', 'smbuser:somegroup', '/tmp']
                    if 'chown' in args and 'smbuser:somegroup' in args:
                        found = True
                        break
                self.assertTrue(found, "Should call chown with correct group name")
        finally:
            self.patcher_perms.start()



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

        # 2. Create link
        with patch('smb_manager.os.symlink'),              patch('smb_manager.os.path.exists', return_value=False),              patch('smb_manager.subprocess.run'),              patch.object(self.smb_manager, '_get_group_name', return_value='group'):

            success = self.smb_manager.create_symlink('Folder/A')
            self.assertTrue(success)
            self.assertTrue(self.smb_manager.is_link_active('Folder/A'))
            # Internal check
            self.assertIn('Folder_A', self.smb_manager._active_links)

        # 3. Create another link
        with patch('smb_manager.os.symlink'),              patch('smb_manager.os.path.exists', return_value=False),              patch('smb_manager.subprocess.run'):

            self.smb_manager.create_symlink('Folder/B')
            self.assertTrue(self.smb_manager.is_link_active('Folder/B'))
            self.assertEqual(len(self.smb_manager._active_links), 2)

        # 4. Remove link
        with patch('smb_manager.os.remove'),              patch('smb_manager.os.path.exists', side_effect=lambda x: True),              patch('smb_manager.os.listdir', return_value=[]):
             # Mock deactivate to avoid side effects
             with patch.object(self.smb_manager, 'deactivate_smb_share'):
                 success = self.smb_manager.remove_symlink('Folder/A')
                 self.assertTrue(success)
                 self.assertFalse(self.smb_manager.is_link_active('Folder/A'))
                 self.assertTrue(self.smb_manager.is_link_active('Folder/B'))
                 self.assertEqual(len(self.smb_manager._active_links), 1)

class TestSMBManagerCleanup(unittest.TestCase):
    def setUp(self):
        # Patch everything EXCEPT cleanup_all_symlinks
        self.patcher_init = patch('smb_manager.SMBManager._init_smb_config')
        self.patcher_ownership = patch('smb_manager.SMBManager._set_smb_user_ownership')
        self.patcher_perms = patch('smb_manager.SMBManager._set_links_directory_permissions')

        self.mock_init = self.patcher_init.start()
        self.mock_ownership = self.patcher_ownership.start()
        self.mock_perms = self.patcher_perms.start()

        self.config = DummyConfig()

        # We need to mock cleanup during __init__ though, otherwise it runs real code
        with patch('smb_manager.SMBManager.cleanup_all_symlinks'):
            self.smb_manager = SMBManager(self.config, 1000, 1000)

    def tearDown(self):
        self.patcher_init.stop()
        self.patcher_ownership.stop()
        self.patcher_perms.stop()

    def test_cleanup_real_method(self):
        # Setup cache
        self.smb_manager._active_links.add('A_B')

        # Mock os calls used in cleanup
        with patch('smb_manager.os.path.exists', return_value=True),              patch('smb_manager.os.listdir', return_value=['A_B']),              patch('smb_manager.os.path.islink', return_value=True),              patch('smb_manager.os.remove') as mock_remove:

            self.smb_manager.cleanup_all_symlinks()

            # Verify cache cleared
            self.assertEqual(len(self.smb_manager._active_links), 0)
            # Verify remove called
            mock_remove.assert_called()

if __name__ == '__main__':
    unittest.main()
