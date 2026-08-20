import os
import sys
import unittest
from unittest.mock import patch, MagicMock, mock_open
import subprocess

# Add app directory to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from nfs_utils import (
    DEFAULT_NFS_OPTIONS,
    DEFAULT_NFS_TIMEOUT,
    build_nfs_mount_command,
    is_nfs_mount_present,
    find_nfs_mount_point,
    mount_nfs,
    unmount_nfs,
)


class TestNfsUtils(unittest.TestCase):

    def test_build_nfs_mount_command_default_options(self):
        """기본 NFS 옵션으로 마운트 명령어가 생성되는지 확인"""
        cmd = build_nfs_mount_command('10.0.0.1:/export/share', '/mnt/gshare')
        expected = [
            'mount', '-t', 'nfs',
            '-o', 'nolock,vers=3,hard,timeo=600,retrans=5,actimeo=30',
            '10.0.0.1:/export/share', '/mnt/gshare'
        ]
        self.assertEqual(cmd, expected)
        self.assertIn('vers=3', cmd[4])
        self.assertIn('nolock', cmd[4])

    def test_build_nfs_mount_command_custom_options(self):
        """커스텀 옵션으로 마운트 명령어가 생성되는지 확인"""
        custom_opt = 'vers=4,hard'
        cmd = build_nfs_mount_command('10.0.0.1:/share', '/mnt/test', options=custom_opt)
        expected = ['mount', '-t', 'nfs', '-o', 'vers=4,hard', '10.0.0.1:/share', '/mnt/test']
        self.assertEqual(cmd, expected)

    @patch('subprocess.run')
    @patch('os.path.exists', return_value=True)
    def test_mount_nfs_success(self, mock_exists, mock_run):
        """mount_nfs 성공 시 returncode == 0 처리 확인"""
        mock_run.return_value = MagicMock(returncode=0, stdout='mounted successfully', stderr='')
        success, msg = mount_nfs('10.0.0.1:/share', '/mnt/gshare')

        self.assertTrue(success)
        mock_run.assert_called_once_with(
            ['mount', '-t', 'nfs', '-o', DEFAULT_NFS_OPTIONS, '10.0.0.1:/share', '/mnt/gshare'],
            capture_output=True,
            text=True,
            timeout=DEFAULT_NFS_TIMEOUT
        )

    @patch('subprocess.run')
    @patch('os.path.exists', return_value=True)
    def test_mount_nfs_failure_returns_stderr(self, mock_exists, mock_run):
        """mount_nfs 실패 시 stderr 내용이 반환 메시지에 포함되는지 확인"""
        stderr_output = 'mount.nfs: access denied by server while mounting 10.0.0.1:/share'
        mock_run.return_value = MagicMock(returncode=32, stdout='', stderr=stderr_output)

        success, msg = mount_nfs('10.0.0.1:/share', '/mnt/gshare')

        self.assertFalse(success)
        self.assertEqual(msg, stderr_output)

    @patch('subprocess.run')
    @patch('os.path.exists', return_value=True)
    def test_mount_nfs_timeout(self, mock_exists, mock_run):
        """mount_nfs 타임아웃 발생 시 처리 확인"""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=['mount'], timeout=30)

        success, msg = mount_nfs('10.0.0.1:/share', '/mnt/gshare', timeout=30)

        self.assertFalse(success)
        self.assertIn('시간 초과', msg)

    @patch('subprocess.run')
    def test_unmount_nfs_success_and_options(self, mock_run):
        """unmount_nfs 명령어 옵션(-f, -l) 및 성공 처리 확인"""
        mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')

        success, msg = unmount_nfs('/mnt/gshare', force=True, lazy=True)

        self.assertTrue(success)
        mock_run.assert_called_once_with(
            ['umount', '-f', '-l', '/mnt/gshare'],
            capture_output=True,
            text=True,
            timeout=15
        )

    def test_is_nfs_mount_present_and_find_nfs_mount_point(self):
        """/proc/mounts 파싱 기반 마운트 검증 테스트"""
        proc_mounts_data = (
            "10.0.0.1:/volume1/video /mnt/gshare nfs rw,relatime,vers=3 0 0\n"
            "192.168.1.50:/data\\040folder /mnt/test\\040dir nfs4 rw 0 0\n"
        )

        with patch('builtins.open', mock_open(read_data=proc_mounts_data)):
            with patch('os.path.realpath', side_effect=lambda x: x):
                # exact match
                self.assertTrue(is_nfs_mount_present('/mnt/gshare', '10.0.0.1:/volume1/video'))
                # export match with trailing slash
                self.assertTrue(is_nfs_mount_present('/mnt/gshare', '10.0.0.1:/volume1/video/'))

                # find_nfs_mount_point
                self.assertEqual(find_nfs_mount_point('10.0.0.1:/volume1/video'), '/mnt/gshare')
                self.assertEqual(find_nfs_mount_point('hostname:/volume1/video'), '/mnt/gshare')
                self.assertEqual(find_nfs_mount_point('192.168.1.50:/data folder'), '/mnt/test dir')
                self.assertIsNone(find_nfs_mount_point('10.0.0.1:/not_mounted'))


class TestWebServerTestNfs(unittest.TestCase):

    def setUp(self):
        from web_server import GshareWebServer
        mock_manager = MagicMock()
        mock_manager.config.MOUNT_PATH = '/mnt/gshare'
        mock_manager.config.NFS_PATH = '10.0.0.1:/volume1/video'

        self.web_server = GshareWebServer()
        self.web_server.manager = mock_manager
        self.app_client = self.web_server.app.test_client()

    def test_option_consistency(self):
        """실제 mount 옵션과 test_nfs에서 사용하는 옵션이 완벽히 동일한지 검증"""
        from main import GShareManager
        manager = GShareManager.__new__(GShareManager)
        manager.config = MagicMock()
        manager.config.MOUNT_PATH = '/mnt/gshare'
        manager.config.NFS_PATH = '10.0.0.1:/volume1/video'

        with patch('main.mount_nfs', return_value=(True, '')) as mock_main_mount:
            with patch('main._is_nfs_mount_present', return_value=False):
                manager._mount_nfs()
                main_options = mock_main_mount.call_args[1].get('options') if mock_main_mount.call_args else None

        with patch('web_server.mount_nfs', return_value=(True, '')) as mock_test_mount:
            with patch('web_server.find_nfs_mount_point', return_value=None):
                mock_scandir_instance = MagicMock()
                mock_scandir_instance.__iter__.return_value = []
                with patch('os.scandir', return_value=mock_scandir_instance):
                    with patch('web_server.unmount_nfs'):
                        self.app_client.post('/test_nfs', data={'nfs_path': '10.0.0.1:/volume1/video'})
                        test_options = mock_test_mount.call_args[1].get('options') if mock_test_mount.call_args else None

        self.assertIsNotNone(main_options)
        self.assertIsNotNone(test_options)
        self.assertEqual(main_options, test_options)
        self.assertEqual(main_options, DEFAULT_NFS_OPTIONS)

    @patch('web_server.find_nfs_mount_point', return_value='/mnt/gshare')
    @patch('os.scandir')
    def test_test_nfs_already_mounted(self, mock_scandir, mock_find_mount):
        """이미 마운트된 경우 기존 마운트 지점에서 읽기 테스트만 수행하는지 확인"""
        mock_scandir_instance = MagicMock()
        mock_scandir_instance.__iter__.return_value = [MagicMock()]
        mock_scandir.return_value = mock_scandir_instance

        res = self.app_client.post('/test_nfs', data={'nfs_path': '10.0.0.1:/volume1/video'})
        data = res.get_json()

        self.assertEqual(res.status_code, 200)
        self.assertEqual(data['status'], 'success')
        self.assertIn('이미 /mnt/gshare에 마운트되어 있으며', data['message'])
        mock_scandir.assert_called_once_with('/mnt/gshare')

    @patch('web_server.find_nfs_mount_point', return_value=None)
    @patch('web_server.is_nfs_mount_present', return_value=False)
    @patch('web_server.mount_nfs')
    @patch('web_server.unmount_nfs')
    @patch('os.scandir')
    def test_test_nfs_new_mount_success(self, mock_scandir, mock_unmount, mock_mount, mock_is_present, mock_find_mount):
        """신규 마운트 성공 및 읽기 성공 테스트 후 unmount cleanup 검증"""
        mock_mount.return_value = (True, 'ok')
        mock_scandir_instance = MagicMock()
        mock_scandir_instance.__iter__.return_value = []
        mock_scandir.return_value = mock_scandir_instance

        res = self.app_client.post('/test_nfs', data={'nfs_path': '10.0.0.1:/volume1/video'})
        data = res.get_json()

        self.assertEqual(res.status_code, 200, f"Expected 200, got {res.status_code}: {data}")
        self.assertEqual(data['status'], 'success')
        self.assertIn('읽기 권한이 정상입니다', data['message'])

        # Verify mount options passed
        mock_mount.assert_called_once()
        _, kwargs = mock_mount.call_args
        self.assertEqual(kwargs['options'], DEFAULT_NFS_OPTIONS)

        # Verify unmount was called in cleanup
        mock_unmount.assert_called_once()

    @patch('web_server.find_nfs_mount_point', return_value=None)
    @patch('web_server.is_nfs_mount_present', return_value=False)
    @patch('web_server.mount_nfs')
    @patch('web_server.unmount_nfs')
    def test_test_nfs_mount_failure_returns_stderr(self, mock_unmount, mock_mount, mock_is_present, mock_find_mount):
        """마운트 실패 시 stderr 오류 메시지가 사용자에게 전달되는지 검증"""
        stderr_msg = 'mount.nfs: Connection refused'
        mock_mount.return_value = (False, stderr_msg)

        res = self.app_client.post('/test_nfs', data={'nfs_path': '10.0.0.1:/volume1/video'})
        data = res.get_json()

        self.assertEqual(res.status_code, 200)
        self.assertEqual(data['status'], 'error')
        self.assertIn(stderr_msg, data['message'])

        # Ensure cleanup unmount was still attempted
        mock_unmount.assert_called_once()


if __name__ == '__main__':
    unittest.main()
