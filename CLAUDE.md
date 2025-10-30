# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**This document contains the full implementation brief for enhancing LMCache's Redis connector.**
**Claude: please read this file before coding. Follow each step sequentially.**

---

## Task: Add batched/pipelined Redis connector to LMCache (parity with Valkey)

### Repos & file locations (current upstream)

**Main repo:** LMCache/LMCache
GitHub: https://github.com/LMCache/LMCache

**Valkey connector (reference behavior):**
- `lmcache/v1/storage_backend/connector/valkey_connector.py`
- `lmcache/v1/storage_backend/connector/valkey_adapter.py`

**Redis connector (to be upgraded):**
- `lmcache/v1/storage_backend/connector/redis_connector.py`
- `lmcache/v1/storage_backend/connector/redis_adapter.py`

**Docs:** Redis and Valkey backend pages (for examples/config)
https://docs.lmcache.ai

---

## What we're doing (overview)

Bring the Redis backend to feature parity with the Valkey backend by adding:

1. **Hash-slot–aware batching** (group keys per cluster slot).
2. **Pipelined multi-key reads/writes** (MGET for reads; pipelined SET EX for writes).
3. **Config knobs** (mode, pool, chunk size) mirroring Valkey docs.

**Goal:** cut round-trips per request from "many GETs" to "~1 per slot", lowering p95/p99 latency for LMCache's remote KV cache offload.

---

## Step-by-step (get started)

### 0) Fork & clone

Fork LMCache/LMCache to lmcache-redis-enhanced (your GitHub).

Clone and branch:
```bash
git clone https://github.com/<YOUR_GH>/lmcache-redis-enhanced.git
cd lmcache-redis-enhanced
git checkout -b feature/redis-batched-connector
```

### 1) Baseline: run current Redis connector

Stand up Redis 8.x:
- Standalone and a 3–6 master cluster (OSS cluster API).

Add a minimal bench script (temporary) that:
- Writes N keys (512B, 2KB, 8KB) with TTL.
- Issues read requests of 32/64/128/256 keys per request (no batching yet).
- Records p50/p95/p99 and approximate round-trips/request.
- Save CSV as `bench/results_baseline.csv`.

### 2) Read Valkey implementation for patterns

Study `valkey_connector.py` and `valkey_adapter.py` to replicate structure (batching, pipelining, slot grouping).

### 3) Implement Redis batching/pipelining

In `redis_adapter.py` and `redis_connector.py`:

**Build async clients:**
- Standalone: `redis.asyncio.from_url(...)`
- Cluster: `redis.asyncio.RedisCluster.from_url(...)`

**Add a slot function (cluster):** `RedisClusterHashSlot.key_slot(key)`.

**mget_batched(keys):**
- Bucket keys by slot → chunk by `chunk_size` (default 256) → pipeline MGET per chunk → return values in original order.

**set_many_with_ttl(items):**
- `(key, value, ttl)` → bucket by slot → chunk → pipeline `SET key value EX ttl`.

**Add config surface** (read from `extra_config` or env):
- `redis_mode`: "standalone"|"cluster"
- `redis_username`, `redis_password`, `redis_database` (standalone)
- `chunk_size` (128–512), `max_connections`, optional `max_inflight`.

### 4) Tests (fast correctness)

Unit tests (pytest):
- Order preservation in `mget_batched`.
- Missing keys → None.
- TTL accuracy (±1s).
- Standalone vs cluster parity.

### 5) Microbench (A/B old vs new)

Create `bench/test_redis_connector.py` that:
- Generates dataset (tagged keys like `lm:{trace}:chunk:i` to co-locate slots).
- Runs baseline connector (old code path) vs new batched connector.
- Sweeps `chunk_size=[64,128,256]` and pool sizes.
- Emits `bench/results_ab.csv` with `p50,p95,p99,ops_sec,bytes_sec,round_trips_per_request`.

**Expected win:** ≥2–5× faster GET phase p95 for 64–256-key reads when keys are tagged to one slot.

### 6) Docs

Update Redis backend docs to show:
- Standalone and cluster examples.
- New knobs (`chunk_size`, pool).
- Note on key-tagging (e.g., `{trace_id}`) to collapse requests to one slot.

### 7) Hardening

- Verify behavior without key tags (cross-slot): confirm more slot groups → more round-trips; still correct.
- Add light retry on transient network errors; rely on redis-py cluster for MOVED/ASK.
- Run a 60–120 min soak at steady concurrency to confirm stability.

### 8) Deliverables

- Modified `redis_adapter.py` / `redis_connector.py` with batching/pipelining.
- `bench/test_redis_connector.py` + results CSV.
- Unit tests under `tests/`.
- Short `RESULTS.md` summarizing env + A/B metrics.
- (Optional) Doc update PR notes.

---

## Success criteria (acceptance)

1. **Functional parity** with existing Redis backend.
2. **Round-trips/request** ≈ number of slot groups (often 1 with tagged keys).
3. **≥50% reduction in p95** vs baseline for 128-key read requests on cluster.
4. **Stable under soak**; no increase in error rates.

---

## Repo structure

```
lmcache-redis-enhanced/
├── CLAUDE.md                # this file - full instruction brief
├── lmcache/
│   └── v1/
│       └── storage_backend/
│           └── connector/
│               ├── redis_adapter.py
│               ├── redis_connector.py
│               ├── valkey_adapter.py
│               ├── valkey_connector.py
├── bench/
│   └── test_redis_connector.py  # benchmark script (to be generated)
└── README.md
```

---

## Development commands

### Testing
```bash
# Run unit tests
pytest tests/

# Run specific test file
pytest tests/test_redis_adapter.py -v

# Run benchmarks
python bench/test_redis_connector.py
```

### Redis setup (Docker)
```bash
# Standalone Redis
docker run -d -p 6379:6379 redis:8

# Redis Cluster (example 3-node)
docker run -d -p 7000-7005:7000-7005 grokzen/redis-cluster:latest
```

---

## Key implementation notes

- **Slot awareness:** Use `RedisClusterHashSlot.key_slot(key)` to group keys by hash slot before batching
- **Order preservation:** Ensure `mget_batched` returns values in the same order as input keys
- **Key tagging:** Recommend users use hash tags like `{trace_id}` in keys to ensure co-location on same slot
- **Error handling:** Handle MOVED/ASK redirects (redis-py handles this), add retries for transient network errors
- **Async operations:** Use `redis.asyncio` for all Redis operations to match existing LMCache patterns
