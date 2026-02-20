## 2026-02-20 - [Proxmox API Request Coalescing]
**Learning:** Sequential API calls to the same endpoint within a tight loop (e.g., monitor loop) can cause significant redundant network traffic. In , 3 separate methods were calling the same endpoint 6 times per loop iteration.
**Action:** Implement short-term caching (TTL) for frequently accessed, slowly changing data like VM status to coalesce multiple logical requests into a single physical request per loop iteration.
## 2026-02-20 - [Proxmox API Request Coalescing]
**Learning:** Sequential API calls to the same endpoint within a tight loop (e.g., monitor loop) can cause significant redundant network traffic. In `ProxmoxAPI`, 3 separate methods were calling the same endpoint 6 times per loop iteration.
**Action:** Implement short-term caching (TTL) for frequently accessed, slowly changing data like VM status to coalesce multiple logical requests into a single physical request per loop iteration.
