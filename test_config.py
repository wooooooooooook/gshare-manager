import unittest
from unittest.mock import MagicMock, patch, mock_open
import sys
import os

# Clean up sys.modules to ensure a fresh start for each test run if needed
# but since we are running via unittest command, it should be fine per process.

# Mock dependencies
sys.modules['requests'] = MagicMock()
sys.modules['urllib3'] = MagicMock()
mock_yaml = MagicMock()
sys.modules['yaml'] = mock_yaml

# Ensure app is in path
sys.path.append(os.path.join(os.getcwd(), 'app'))

# Import GshareConfig after mocking
from config import GshareConfig

class TestGshareConfig(unittest.TestCase):
    def setUp(self):
        # Reset the mock_yaml for each test
        mock_yaml.reset_mock()
        mock_yaml.safe_load.side_effect = None
        mock_yaml.safe_load.return_value = None

    @patch('os.path.exists')
    def test_load_config_file_not_found(self, mock_exists):
        """Test that load_config raises ValueError when config file is missing"""
        mock_exists.return_value = False

        with self.assertRaises(ValueError) as cm:
            GshareConfig.load_config()

        self.assertEqual(str(cm.exception), "YAML 설정 파일을 로드할 수 없습니다.")

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_config_invalid_yaml(self, mock_file_open, mock_exists):
        """Test that load_config raises ValueError when YAML is invalid"""
        mock_exists.return_value = True

        # Patch yaml.safe_load via the already mocked module
        mock_yaml.safe_load.side_effect = Exception("YAML Error")

        with patch.dict('os.environ', {'LOG_LEVEL': 'INFO'}):
            with self.assertRaises(ValueError) as cm:
                GshareConfig.load_config()

        self.assertEqual(str(cm.exception), "YAML 설정 파일을 로드할 수 없습니다.")

if __name__ == '__main__':
    unittest.main()
