import time
import os
import sys
import shutil
import tempfile
import argparse
import subprocess
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

def run_native_scan(monitor):
    start_time = time.perf_counter()
    result = monitor._scan_folders()
    end_time = time.perf_counter()
    return end_time - start_time, len(result)

def run_find_scan(path):
    start_time = time.perf_counter()
    try:
        # find path -type f -mtime -1
        cmd = ['find', path, '-type', 'f', '-mtime', '-1']
        process = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end_time = time.perf_counter()
        if process.returncode != 0:
            return None, 0
        return end_time - start_time, 0
    except Exception as e:
        print(f"Error running find: {e}")
        return None, 0

def run_hybrid_scan(path):
    """Run find and parse output in Python (avoids shell pipe overhead)"""
    start_time = time.perf_counter()
    try:
        # find path -type f -mtime -1 -print0
        cmd = ['find', path, '-type', 'f', '-mtime', '-1', '-print0']
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        if process.returncode != 0:
            return None, 0

        # Parse null-terminated strings
        files = process.stdout.split(b'\0')
        folders = set()
        for f in files:
            if f:
                folders.add(os.path.dirname(f))

        end_time = time.perf_counter()
        return end_time - start_time, len(folders)
    except Exception as e:
        print(f"Error running hybrid scan: {e}")
        return None, 0

def benchmark(target_path=None):
    temp_dir = None
    if not target_path:
        print("No target path provided. Creating dummy structure...")
        temp_dir = tempfile.mkdtemp()
        create_dummy_structure(temp_dir, depth=3, folders_per_level=5, files_per_folder=10)
        target_path = temp_dir
        print(f"Created dummy structure in {target_path}")
    else:
        if not os.path.exists(target_path):
            print(f"Error: Path {target_path} does not exist.")
            return

    try:
        print(f"Benchmarking scan on: {target_path}")
        print("-" * 65)
        print(f"{'Method':<35} | {'Time (s)':<10} | {'Found (Folders)'}")
        print("-" * 65)

        # 1. Native Python Scan
        config = MockConfig(target_path)
        with patch('main.SMBManager') as MockSMB:
            mock_smb_instance = MockSMB.return_value
            mock_smb_instance.links_dir = "/tmp/links"
            mock_proxmox = MagicMock()

            with patch('main.FolderMonitor._get_nfs_ownership', return_value=(1000, 1000)):
                monitor = FolderMonitor(config, mock_proxmox, 0.0)
                duration, count = run_native_scan(monitor)
                print(f"{'Python Native (os.scandir)':<35} | {duration:<10.4f} | {count}")

        # 2. Hybrid Scan (Find + Python Parse)
        duration, count = run_hybrid_scan(target_path)
        if duration is not None:
             print(f"{'Hybrid (find ... -print0 -> python)':<35} | {duration:<10.4f} | {count}")
        else:
             print(f"{'Hybrid (find ... -print0 -> python)':<35} | {'FAILED':<10} | -")

        # 3. Shell Find (Raw)
        duration, _ = run_find_scan(target_path)
        if duration is not None:
             print(f"{'Shell (find ... -type f)':<35} | {duration:<10.4f} | {'(raw file scan)'}")
        else:
             print(f"{'Shell (find ... -type f)':<35} | {'FAILED':<10} | -")

        print("-" * 65)
        print("Analysis:")
        print("1. Python Native: Iterates directories, checks mtime of *folders*.")
        print("2. Hybrid: Uses 'find' to get *files* changed in 24h, then extracts folders.")
        print("3. Shell: Raw execution time of 'find' command.")
        print("")
        print("If 'Hybrid' is significantly faster than 'Python Native', switching is recommended.")
        print("Note: 'find' works best on NFS if metadata caching is enabled.")

    finally:
        if temp_dir:
            shutil.rmtree(temp_dir)
            print("Cleaned up temp files.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Benchmark GShare Folder Scan vs Shell Find')
    parser.add_argument('--path', type=str, help='Target directory path to scan (optional)')
    args = parser.parse_args()

    benchmark(args.path)
