## 2026-02-20 - [Proxmox API Request Coalescing]
**Learning:** Sequential API calls to the same endpoint within a tight loop (e.g., monitor loop) can cause significant redundant network traffic. In , 3 separate methods were calling the same endpoint 6 times per loop iteration.
**Action:** Implement short-term caching (TTL) for frequently accessed, slowly changing data like VM status to coalesce multiple logical requests into a single physical request per loop iteration.
## 2026-02-20 - [Proxmox API Request Coalescing]
**Learning:** Sequential API calls to the same endpoint within a tight loop (e.g., monitor loop) can cause significant redundant network traffic. In `ProxmoxAPI`, 3 separate methods were calling the same endpoint 6 times per loop iteration.
**Action:** Implement short-term caching (TTL) for frequently accessed, slowly changing data like VM status to coalesce multiple logical requests into a single physical request per loop iteration.

## 2025-05-18 - [FolderMonitor Perf]
**Learning:** Frequent syscalls like `os.path.exists` in a tight loop (e.g., status reporting every tick for thousands of items) can block the event loop and degrade performance.
**Action:** Use memory caching for file status (e.g., `set` of active symlinks) when possible, updating the cache on file operations instead of polling the disk.

## 2026-02-20 - [Mount Target Selection Complexity]
**Learning:** `FolderMonitor._filter_mount_targets` had an O(n²) child-folder detection loop (`for path` × `for other in folder_set`) that can dominate scan cycles when many folders change at once.
**Action:** Keep path collections lexicographically sorted and use binary search prefix checks for descendant existence to keep the hot path closer to O(n log n).

## 2026-02-20 - [Timezone Object Reuse in Monitor Loop]
**Learning:** `pytz.timezone(self.config.TIMEZONE)` was being recreated repeatedly in hot paths (`update_state`, folder change formatting). In this codebase's monitor-driven architecture, that adds avoidable lookup overhead every cycle.
**Action:** Cache timezone objects once per manager/monitor instance (`self.local_tz`) and reuse for all timestamp formatting/conversion in looped code paths.
