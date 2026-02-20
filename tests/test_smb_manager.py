import unittest
from unittest.mock import patch, mock_open, MagicMock
import sys
import os

# Add app directory to sys.path
sys.path.append(os.path.join(os.getcwd(), 'app'))

# Mock yaml before importing config
sys.modules['yaml'] = MagicMock()

from smb_manager import SMBManager

class TestSMBManager(unittest.TestCase):
    def setUp(self):
        self.mock_config = MagicMock()
        self.mock_config.SMB_LINKS_DIR = '/tmp/links'
        self.mock_config.SMB_PORT = 445
        self.mock_config.SMB_USERNAME = 'user'
        self.mock_config.SMB_PASSWORD = 'password'
        self.mock_config.SMB_SHARE_NAME = 'share'
        self.mock_config.SMB_COMMENT = 'comment'
        self.mock_config.SMB_GUEST_OK = False
        self.mock_config.MOUNT_PATH = '/mnt/data'

    @patch('builtins.open', new_callable=mock_open)
    @patch('subprocess.run')
    @patch('os.makedirs')
    @patch('os.chmod')
    @patch('os.path.exists')
    def test_init_smb_config(self, mock_exists, mock_chmod, mock_makedirs, mock_run, mock_file):
        mock_run.return_value.returncode = 0
        mock_exists.return_value = False

        with patch.object(SMBManager, '_set_smb_user_ownership'),              patch.object(SMBManager, '_set_links_directory_permissions'),              patch.object(SMBManager, 'cleanup_all_symlinks'):

            manager = SMBManager(self.mock_config, 1000, 1000)

            # Check if open was called with the expected path
            calls = mock_file.call_args_list
            smb_conf_calls = [c for c in calls if c[0][0] == '/etc/samba/smb.conf']
            self.assertTrue(len(smb_conf_calls) > 0, "Should have opened /etc/samba/smb.conf")

    @patch('builtins.open', new_callable=mock_open, read_data='[share]\npath=/tmp')
    def test_check_smb_status(self, mock_file):
        with patch.object(SMBManager, '__init__', return_value=None):
            manager = SMBManager(self.mock_config, 1000, 1000)
            manager.config = self.mock_config

            manager.check_smb_status()

            mock_file.assert_called_with('/etc/samba/smb.conf', 'r')

if __name__ == '__main__':
    unittest.main()
