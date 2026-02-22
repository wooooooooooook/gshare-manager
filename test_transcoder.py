import unittest
from unittest.mock import MagicMock, patch, mock_open
import sys
import os

# Mock dependencies
sys.modules['requests'] = MagicMock()
sys.modules['urllib3'] = MagicMock()
sys.modules['yaml'] = MagicMock()

# Ensure app is in path
sys.path.append(os.getcwd())

# Import Transcoder
# We need to mock config imports inside transcoder if they fail, but they seem to use config.py which is local.
# config.py imports yaml, so we mocked it.

from app.transcoder import Transcoder  # noqa: E402
from app.config import GshareConfig  # noqa: E402

class TestTranscoderOptimization(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock(spec=GshareConfig)
        self.config.TRANSCODING_ENABLED = True
        self.config.TRANSCODING_RULES = []
        self.config.TRANSCODING_DONE_FILENAME = '.transcoding_done'

        # Patch threading to avoid starting threads
        with patch('threading.Thread'), patch('app.transcoder.Transcoder._build_optimized_rules'):
            self.transcoder = Transcoder(self.config)

    @patch('os.stat')
    @patch('builtins.open', new_callable=mock_open, read_data="file1.mp4\nfile2.mp4")
    def test_load_done_list_caching(self, mock_file, mock_stat):
        """Test that _load_done_list caches results based on mtime"""
        directory = '/tmp/test'
        done_file = os.path.join(directory, '.transcoding_done')

        # Setup stat mock
        mock_stat.return_value.st_mtime = 100.0

        # First call - should read file
        result1 = self.transcoder._load_done_list(directory)
        self.assertEqual(result1, frozenset({'file1.mp4', 'file2.mp4'}))
        mock_stat.assert_called_with(done_file)
        mock_file.assert_called_with(done_file, 'r', encoding='utf-8')

        # Reset mock_file to verify it's not called again
        mock_file.reset_mock()

        # Second call with same mtime - should hit cache
        result2 = self.transcoder._load_done_list(directory)
        self.assertEqual(result2, frozenset({'file1.mp4', 'file2.mp4'}))
        mock_stat.assert_called_with(done_file)
        mock_file.assert_not_called()  # Should not open file again

        # Third call with DIFFERENT mtime - should read file again
        mock_stat.return_value.st_mtime = 200.0
        result3 = self.transcoder._load_done_list(directory)
        self.assertEqual(result3, frozenset({'file1.mp4', 'file2.mp4'}))
        mock_stat.assert_called_with(done_file)
        mock_file.assert_called() # Should open file again

    @patch('os.walk')
    @patch('app.transcoder.Transcoder._load_done_list')
    def test_iter_walk_matches_optimization(self, mock_load_done, mock_walk):
        """Test that _iter_walk_matches skips _load_done_list if done file is missing"""
        scan_root = '/mnt/gshare'

        # Case 1: .transcoding_done is present
        mock_walk.return_value = [
            ('/mnt/gshare', [], ['movie.mkv', '.transcoding_done'])
        ]
        mock_load_done.return_value = frozenset()

        # Consume generator
        list(self.transcoder._iter_walk_matches(scan_root))

        # Should call _load_done_list
        mock_load_done.assert_called_with('/mnt/gshare')

        # Reset mocks
        mock_load_done.reset_mock()

        # Case 2: .transcoding_done is MISSING
        mock_walk.return_value = [
            ('/mnt/gshare', [], ['movie.mkv'])
        ]

        # Consume generator
        list(self.transcoder._iter_walk_matches(scan_root))

        # Should NOT call _load_done_list
        mock_load_done.assert_not_called()

if __name__ == '__main__':
    unittest.main()
