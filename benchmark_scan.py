import time
import os
import sys
import shutil
import tempfile
import argparse
import subprocess
from unittest.mock import MagicMock, patch

sys.path.append(os.path.join(os.getcwd(), 'app'))

sys.modules['proxmox_api'] = MagicMock()
sys.modules['mqtt_manager'] = MagicMock()
sys.modules['web_server'] = MagicMock()
sys.modules['transcoder'] = MagicMock()
sys.modules['requests'] = MagicMock()
sys.modules['urllib3'] = MagicMock()
sys.modules['yaml'] = MagicMock()
sys.modules['pytz'] = MagicMock()
sys.modules['smb_manager'] = MagicMock()

try:
    from main import FolderMonitor
except ImportError:
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
    if depth == 0:
        return
    for i in range(folders_per_level):
        folder_path = os.path.join(root_path, f"folder_{depth}_{i}")
        os.makedirs(folder_path, exist_ok=True)
        for j in range(files_per_folder):
            with open(os.path.join(folder_path, f"file_{j}.txt"), 'w') as f:
                f.write("content")
        create_dummy_structure(folder_path, depth - 1, folders_per_level, files_per_folder)

def benchmark(target_path=None):
    temp_dir = None
    if not target_path:
        print("Creating dummy structure...")
        temp_dir = tempfile.mkdtemp()
        create_dummy_structure(temp_dir, depth=3, folders_per_level=5, files_per_folder=10)
        target_path = temp_dir
    else:
        if not os.path.exists(target_path):
            print(f"Error: {target_path} not found")
            return

    try:
        print(f"Benchmarking on: {target_path}")
        print("-" * 60)
        print(f"{'Method':<30} | {'Time (s)':<10} | {'Found'}")
        print("-" * 60)

        config = MockConfig(target_path)
        with patch('main.FolderMonitor._get_nfs_ownership', return_value=(1000, 1000)):
            monitor = FolderMonitor(config, MagicMock(), 0.0)

            # Old
            t0 = time.perf_counter()
            res1 = monitor._scan_folders_iterative(config.MOUNT_PATH, 0.0)
            t1 = time.perf_counter()
            print(f"{'Old Python':<30} | {t1-t0:<10.4f} | {len(res1)}")

            # New
            t0 = time.perf_counter()
            res2 = monitor._scan_folders_hybrid(config.MOUNT_PATH)
            t1 = time.perf_counter()
            count = len(res2) if res2 else 0
            print(f"{'New Hybrid (find)':<30} | {t1-t0:<10.4f} | {count}")

        print("-" * 60)

    finally:
        if temp_dir:
            shutil.rmtree(temp_dir)
            print("Cleanup done.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', type=str)
    args = parser.parse_args()
    benchmark(args.path)
