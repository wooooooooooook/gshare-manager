import os
import time
import logging

# Mock SocketIO
class MockSocketIO:
    def emit(self, event, data):
        pass # Do nothing, just consume CPU/Memory for string passing

# Mock WebServer with original method
class OriginalWebServer:
    def __init__(self, log_file):
        self.log_file = log_file
        self.socketio = MockSocketIO()

    def emit_log_update(self):
        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r', encoding='utf-8', errors='replace') as file:
                    log_content = file.read()
                    self.socketio.emit('log_update', log_content)
                    return True
            return False
        except Exception as e:
            print(f"Error: {e}")
            return False

# Mock WebServer with optimized method (Conceptual implementation for comparison)
class OptimizedWebServer:
    def __init__(self, log_file):
        self.log_file = log_file
        self.socketio = MockSocketIO()
        self.max_log_size = 50 * 1024  # 50KB limit

    def emit_log_update(self):
        try:
            if os.path.exists(self.log_file):
                file_size = os.path.getsize(self.log_file)
                mode = 'r'
                if file_size > self.max_log_size:
                    # Read only the last part
                    with open(self.log_file, 'rb') as file:
                        file.seek(-self.max_log_size, 2)
                        content_bytes = file.read()
                        log_content = content_bytes.decode('utf-8', errors='replace')
                        # Discard partial first line
                        if '\n' in log_content:
                            log_content = log_content.split('\n', 1)[1]
                else:
                    # Read normally
                    with open(self.log_file, 'r', encoding='utf-8', errors='replace') as file:
                        log_content = file.read()

                self.socketio.emit('log_update', log_content)
                return True
            return False
        except Exception as e:
            print(f"Error: {e}")
            return False

def create_dummy_log(filename, size_mb):
    with open(filename, 'w') as f:
        # 1MB chunk
        chunk = "This is a log line. " * 50 + "\n"
        # Approx 1KB per line
        lines = int((size_mb * 1024 * 1024) / len(chunk))
        for _ in range(lines):
            f.write(chunk)

def run_benchmark():
    log_file = "benchmark_test.log"
    print("Creating 50MB log file...")
    create_dummy_log(log_file, 50)

    print("\n--- Original Implementation ---")
    server = OriginalWebServer(log_file)
    start_time = time.time()
    for _ in range(5):
        server.emit_log_update()
    duration = time.time() - start_time
    print(f"Original: {duration:.4f} seconds for 5 iterations")

    print("\n--- Optimized Implementation ---")
    server_opt = OptimizedWebServer(log_file)
    start_time = time.time()
    for _ in range(5):
        server_opt.emit_log_update()
    duration = time.time() - start_time
    print(f"Optimized: {duration:.4f} seconds for 5 iterations")

    os.remove(log_file)

if __name__ == "__main__":
    run_benchmark()
