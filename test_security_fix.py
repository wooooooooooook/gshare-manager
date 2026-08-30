import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Mock EVERY dependency that might be missing
sys.modules['flask'] = MagicMock()
sys.modules['flask_socketio'] = MagicMock()
sys.modules['pytz'] = MagicMock()
sys.modules['yaml'] = MagicMock()
sys.modules['requests'] = MagicMock()
sys.modules['socketio'] = MagicMock()
sys.modules['engineio'] = MagicMock()
sys.modules['paho'] = MagicMock()
sys.modules['paho.mqtt'] = MagicMock()
sys.modules['paho.mqtt.client'] = MagicMock()

# Mock config module before importing web_server
mock_config_mod = MagicMock()
sys.modules['config'] = mock_config_mod

# Now import GshareWebServer
from app.web_server import GshareWebServer

class TestSecurityFix(unittest.TestCase):
    def setUp(self):
        # We need to mock the Flask app object that GshareWebServer creates
        with patch('app.web_server.Flask') as mock_flask:
            self.mock_app = mock_flask.return_value
            self.server = GshareWebServer()
            self.server.app = self.mock_app

    @patch('app.web_server.subprocess.run')
    @patch('app.web_server.request')
    @patch('app.web_server.jsonify')
    @patch('app.web_server.tempfile.TemporaryDirectory')
    def test_nfs_command_injection_fix(self, mock_tempdir, mock_jsonify, mock_request, mock_run):
        # Setup tempdir mock
        mock_tempdir.return_value.__enter__.return_value = "/tmp/mock_mount"

        # Mocking subprocess.run return values
        # First call is mount check, returning empty stdout
        mock_run_res = MagicMock(stdout="", stderr="", returncode=1)
        mock_run.return_value = mock_run_res

        # Malicious nfs_path from request.form.get
        malicious_path = "; touch /tmp/pwned ;"
        mock_request.form.get.return_value = malicious_path

        # Simulate test_nfs call
        self.server.test_nfs()

        # Verify that subprocess.run was called with a list and shell=False
        calls = mock_run.call_args_list

        print(f"Subprocess calls: {calls}")

        # We expect at least:
        # 1. subprocess.run(['mount', '-t', 'nfs'], ...)
        # 2. subprocess.run(['mount', '-t', 'nfs', '-o', ..., malicious_path, ...], ...)
        # 3. subprocess.run(['umount', ...], ...)

        self.assertTrue(len(calls) >= 2, f"Expected at least 2 subprocess calls, got {len(calls)}")

        # Check if any call used shell=True
        for call in calls:
            args, kwargs = call
            cmd = args[0]
            self.assertFalse(kwargs.get('shell', False), f"Subprocess call used shell=True: {call}")
            self.assertIsInstance(cmd, list, f"Subprocess command is not a list: {call}")

        # Verify the specific mount call contains the malicious path as a single argument
        found_mount_call = False
        for call in calls:
            args, kwargs = call
            cmd_list = args[0]
            if len(cmd_list) > 3 and cmd_list[0] == 'mount' and cmd_list[1] == '-t' and cmd_list[2] == 'nfs':
                found_mount_call = True
                self.assertIn(malicious_path, cmd_list)
                # Ensure the malicious path is a separate element in the list
                # mount_cmd = ["mount", "-t", "nfs", "-o", "nolock,vers=3,soft,timeo=100", nfs_path, temp_mount]
                # It should be at index 5
                self.assertEqual(cmd_list[5], malicious_path)

        self.assertTrue(found_mount_call, "Did not find the expected mount call with options")

if __name__ == '__main__':
    unittest.main()
