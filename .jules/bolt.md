## 2026-02-20 - [Proxmox API Request Coalescing]
**Learning:** Sequential API calls to the same endpoint within a tight loop (e.g., monitor loop) can cause significant redundant network traffic. In , 3 separate methods were calling the same endpoint 6 times per loop iteration.
**Action:** Implement short-term caching (TTL) for frequently accessed, slowly changing data like VM status to coalesce multiple logical requests into a single physical request per loop iteration.
## 2026-02-20 - [Proxmox API Request Coalescing]
**Learning:** Sequential API calls to the same endpoint within a tight loop (e.g., monitor loop) can cause significant redundant network traffic. In `ProxmoxAPI`, 3 separate methods were calling the same endpoint 6 times per loop iteration.
**Action:** Implement short-term caching (TTL) for frequently accessed, slowly changing data like VM status to coalesce multiple logical requests into a single physical request per loop iteration.

## 2025-05-18 - [FolderMonitor Perf]
**Learning:** Frequent syscalls like `os.path.exists` in a tight loop (e.g., status reporting every tick for thousands of items) can block the event loop and degrade performance.
**Action:** Use memory caching for file status (e.g., `set` of active symlinks) when possible, updating the cache on file operations instead of polling the disk.
