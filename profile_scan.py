import cProfile
import pstats
import os
import sys
import shutil
import tempfile
import time
import argparse
from unittest.mock import MagicMock, patch

# Add app to path
sys.path.append(os.path.join(os.getcwd(), 'app'))

# Mock imports to avoid side effects and missing dependencies
sys.modules['proxmox_api'] = MagicMock()
sys.modules['mqtt_manager'] = MagicMock()
sys.modules['web_server'] = MagicMock()
sys.modules['transcoder'] = MagicMock()
sys.modules['requests'] = MagicMock()
sys.modules['urllib3'] = MagicMock()
sys.modules['yaml'] = MagicMock()
sys.modules['pytz'] = MagicMock()

try:
    from main import FolderMonitor
except ImportError as e:
    print(f"ImportError: {e}")
    sys.path.append('app')
    from main import FolderMonitor

class MockConfig:
    def __init__(self, mount_path):
        self.MOUNT_PATH = mount_path
        self.SMB_LINKS_DIR = "/tmp/links"
        self.TIMEZONE = "UTC"
        self.TRANSCODING_ENABLED = False
        self.TRANSCODING_RULES = []
        self.SMB_USERNAME = "test"
        self.SMB_PASSWORD = "test"
        self.SMB_SHARE_NAME = "test"
        self.SMB_COMMENT = "test"
        self.SMB_GUEST_OK = False
        self.SMB_PORT = 445
        self.NFS_PATH = None

def create_dummy_structure(root_path, depth=3, folders_per_level=5, files_per_folder=10):
    """Creates a nested folder structure."""
    if depth == 0:
        return

    for i in range(folders_per_level):
        folder_path = os.path.join(root_path, f"folder_{depth}_{i}")
        os.makedirs(folder_path, exist_ok=True)
        for j in range(files_per_folder):
            with open(os.path.join(folder_path, f"file_{j}.txt"), 'w') as f:
                f.write("content")
        create_dummy_structure(folder_path, depth - 1, folders_per_level, files_per_folder)

def profile_scan(target_path=None):
    temp_dir = None
    if not target_path:
        print("No target path provided. Creating dummy structure...")
        temp_dir = tempfile.mkdtemp()
        create_dummy_structure(temp_dir, depth=3, folders_per_level=5, files_per_folder=20)
        target_path = temp_dir
        print(f"Created dummy structure in {target_path}")
    else:
        if not os.path.exists(target_path):
            print(f"Error: Path {target_path} does not exist.")
            return

    try:
        config = MockConfig(target_path)

        # Mock SMBManager
        with patch('main.SMBManager') as MockSMB:
            mock_smb_instance = MockSMB.return_value
            mock_smb_instance.links_dir = "/tmp/links"

            mock_proxmox = MagicMock()

            # Use patch for FolderMonitor methods
            with patch('main.FolderMonitor._get_nfs_ownership', return_value=(1000, 1000)):
                print(f"Initializing FolderMonitor scanning {target_path}...")
                monitor = FolderMonitor(config, mock_proxmox, 0.0)

                print("Starting cProfile...")
                profiler = cProfile.Profile()
                profiler.enable()

                start_time = time.time()
                monitor._scan_folders()
                end_time = time.time()

                profiler.disable()
                print(f"Scan finished in {end_time - start_time:.4f} seconds.")

                stats = pstats.Stats(profiler)
                print("\n" + "="*60)
                print("Top 20 functions by CUMULATIVE TIME")
                print("(Shows where the total time is spent, including sub-calls)")
                print("="*60)
                stats.sort_stats('cumtime').print_stats(20)

                print("\n" + "="*60)
                print("Top 20 functions by TOTAL TIME (Internal Time)")
                print("(Shows which specific functions are slow themselves)")
                print("="*60)
                stats.sort_stats('tottime').print_stats(20)

    finally:
        if temp_dir:
            shutil.rmtree(temp_dir)
            print("Cleaned up temp files.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Profile GShare Folder Scan')
    parser.add_argument('--path', type=str, help='Target directory path to scan (optional)')
    args = parser.parse_args()

    profile_scan(args.path)
