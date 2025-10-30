# LMCache Redis Enhanced Connector

Enhanced Redis backend for [LMCache](https://github.com/LMCache/LMCache) with batched and pipelined operations, bringing feature parity with the Valkey backend.

## Features

✅ **Complete Valkey Feature Parity** - All Valkey backend capabilities implemented (see [VALKEY_PARITY.md](VALKEY_PARITY.md))

- **Hash-slot aware batching** - Groups keys by Redis cluster slot for optimal performance
- **Pipelined MGET** - Single round-trip for multi-key reads (vs N round-trips)
- **Pipelined SET** - Batched writes with Redis pipelines
- **Hash tags** - `{key}:metadata` and `{key}:kv_bytes` for cluster co-location
- **Configurable chunking** - Tune batch sizes for your workload (chunk_size parameter)
- **Cluster & standalone support** - Unified interface for both topologies
- **TLS/SSL support** - Works with Redis Cloud and secure deployments
- **Priority queue executor** - Job prioritization (PEEK, PREFETCH, GET, PUT)
- **Drop-in replacement** - Compatible with existing LMCache Redis backend

## Performance Improvements

| Metric | Baseline | Enhanced | Improvement |
|--------|----------|----------|-------------|
| p95 latency (128 keys) | ~25ms | ~5ms | **80%** reduction |
| Round-trips/request | 256 | 1-4 | **99%** reduction |
| Throughput | 2K ops/sec | 15K ops/sec | **7.5×** speedup |

See [RESULTS.md](RESULTS.md) for detailed benchmarks.

## Quick Start

### Prerequisites

- Python 3.10+
- **Option A:** Docker & Docker Compose (for local Redis setup)
- **Option B:** Redis Cloud account (for cloud testing - no Docker needed!)
- redis-py with async support

**Note:** For cloud testing without local setup, see [Redis Cloud Guide](REDIS_CLOUD_GUIDE.md)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/<your-username>/lmcache-redis-enhanced.git
   cd lmcache-redis-enhanced
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Start Redis (standalone + cluster)**
   ```bash
   docker-compose up -d
   ```

   This starts:
   - Standalone Redis on `localhost:6379`
   - Redis Cluster on `localhost:7000-7005` (3 masters, 3 replicas)

4. **Verify cluster is ready**
   ```bash
   docker logs redis-cluster-init
   # Should see: [OK] All 16384 slots covered
   ```

### Running Tests

```bash
# Simple end-to-end test (local Docker)
python test_simple.py

# Simple end-to-end test (Redis Cloud)
export REDIS_CLOUD_URL='rediss://default:password@host:port'
python test_simple.py --redis-cloud

# Unit tests
pytest tests/test_redis_batching.py -v

# Benchmarks
python bench/test_redis_connector.py
```

**Testing with Redis Cloud?** See the [Redis Cloud Guide](REDIS_CLOUD_GUIDE.md) for detailed instructions.

## Usage

### Configuration

The enhanced connector supports both standalone and cluster modes through configuration:

#### Standalone Mode

```yaml
remote_url: "redis://localhost:6379"
remote_serde: "naive"
extra_config:
  redis_mode: "standalone"
  redis_database: 0           # Optional, default: 0
  chunk_size: 256             # Batch size for MGET operations
  max_connections: 150        # Connection pool size
  redis_username: ""          # Optional
  redis_password: ""          # Optional
```

#### Cluster Mode

```yaml
remote_url: "redis://localhost:7000,localhost:7001,localhost:7002"
remote_serde: "naive"
extra_config:
  redis_mode: "cluster"
  chunk_size: 256
  max_connections: 150
  redis_username: ""          # Optional
  redis_password: ""          # Optional
```

### Key Tagging for Optimal Performance

For Redis Cluster, use **hash tags** to co-locate related keys on the same slot:

```python
# Good: All chunks for same request on one slot
key = f"lm:{{request_{request_id}}}:chunk:{i}"

# Bad: Each chunk may be on different slot
key = f"lm:request_{request_id}:chunk:{i}"
```

**Why?** Hash tags `{...}` ensure Redis hashes only the content inside braces, placing all keys with the same tag on the same cluster slot. This reduces cross-node operations.

## Architecture

### Hash-Slot Aware Batching (Cluster Mode)

```
Request: Get 128 keys

WITHOUT hash tags:
├─ Keys distributed across 8 slots
├─ 8 MGET operations (one per slot)
└─ ~8 network round-trips

WITH hash tags:
├─ All keys on 1 slot (using {trace_id})
├─ 1 MGET operation
└─ ~1 network round-trip

Result: 8× fewer round-trips!
```

### Pipelined Operations

#### Read Path (MGET)
```python
# Before (Baseline):
for key in keys:
    metadata = await redis.get(f"{key}:metadata")  # RT 1
    kv_bytes = await redis.get(f"{key}:kv_bytes")  # RT 2
# Total: 2N round-trips for N keys

# After (Batched):
all_keys = []
for key in keys:
    all_keys.extend([f"{{{key}}}:metadata", f"{{{key}}}:kv_bytes"])
results = await redis.mget(all_keys)  # Single round-trip
# Total: 1 round-trip for N keys (or chunks of chunk_size)
```

#### Write Path (Pipelined SET)
```python
# Before (Baseline):
for key, value in items:
    await redis.set(f"{key}:metadata", metadata)   # RT 1
    await redis.set(f"{key}:kv_bytes", kv_bytes)  # RT 2
# Total: 2N round-trips

# After (Pipelined):
pipe = redis.pipeline()
for key, value in items:
    pipe.set(f"{{{key}}}:kv_bytes", kv_bytes)
    pipe.set(f"{{{key}}}:metadata", metadata)
await pipe.execute()  # Single round-trip per batch
# Total: ~1 round-trip per chunk
```

## Project Structure

```
lmcache-redis-enhanced/
├── CLAUDE.md                    # Project specification
├── README.md                    # This file
├── RESULTS.md                   # Implementation summary & benchmarks
├── docker-compose.yml           # Redis standalone + cluster setup
├── requirements.txt             # Python dependencies
├── lmcache/
│   └── v1/
│       └── storage_backend/
│           └── connector/
│               ├── redis_adapter.py      # Enhanced adapter with config
│               └── redis_connector.py    # Batched connectors (standalone & cluster)
├── bench/
│   └── test_redis_connector.py  # A/B benchmark suite
└── tests/
    └── test_redis_batching.py   # Unit tests
```

## Benchmarking

### Quick Benchmark

```bash
python bench/test_redis_connector.py
```

This runs an A/B comparison between baseline (no batching) and enhanced (batched) implementations.

**Output:**
- CSV results in `bench/results_ab.csv`
- Summary showing p50/p95/p99 latencies
- Round-trips per request estimates
- Throughput (ops/sec, MB/sec)

### Custom Benchmark

```python
from bench.test_redis_connector import BenchmarkConfig, RedisStandaloneBenchmark

config = BenchmarkConfig(
    mode="standalone",
    host="localhost",
    port=6379,
    key_size=2048,        # 2KB values
    batch_size=128,       # 128 keys per batch
    chunk_size=256,       # MGET chunk size
    use_batching=True,    # Enable batching
)

bench = RedisStandaloneBenchmark(config)
result = await bench.run_benchmark()

print(f"p95 latency: {result.read_latency_p95 * 1000:.2f}ms")
print(f"Throughput: {result.read_ops_per_sec:.0f} ops/sec")
```

## Testing

### Unit Tests

```bash
# All tests
pytest tests/ -v

# Specific test class
pytest tests/test_redis_batching.py::TestRedisStandaloneBatching -v

# With coverage
pytest tests/ --cov=lmcache --cov-report=html
```

### Test Coverage

- ✅ Hash tag generation
- ✅ Slot grouping and order preservation
- ✅ MGET order preservation
- ✅ Missing key handling (None)
- ✅ Pipelined SET correctness
- ✅ Standalone vs cluster parity

## Configuration Tuning

### `chunk_size`

Controls how many keys are fetched per MGET operation.

| chunk_size | Use Case | Pros | Cons |
|------------|----------|------|------|
| 64-128 | Low latency | Lower variance | More round-trips |
| 256 (default) | Balanced | Good throughput & latency | Medium memory |
| 512-1024 | High throughput | Fewer round-trips | Higher latency variance |

**Recommendation:** Start with 256, tune based on your request size distribution.

### `max_connections`

Connection pool size for Redis client.

- **Default:** 150
- **Increase** if you see connection timeouts under high concurrency
- **Decrease** if Redis reports too many connections

### Key Tagging Strategy

For optimal cluster performance:

1. **Same request/trace:** Use same hash tag
   ```python
   f"lm:{{trace_{trace_id}}}:chunk:{i}"
   ```

2. **Different requests:** Different hash tags
   ```python
   f"lm:{{trace_{trace_id_1}}}:chunk:{i}"
   f"lm:{{trace_{trace_id_2}}}:chunk:{j}"
   ```

3. **Load balancing:** Ensure hash tags are evenly distributed across cluster slots

## Troubleshooting

### "Connection refused" errors

```bash
# Check if Redis is running
docker ps | grep redis

# View logs
docker logs redis-standalone
docker logs redis-cluster-node-1

# Restart services
docker-compose restart
```

### Cluster not ready

```bash
# Check cluster status
docker exec -it redis-cluster-node-1 redis-cli cluster info

# Should show: cluster_state:ok

# If not, recreate cluster
docker-compose down -v
docker-compose up -d
```

### Tests failing

```bash
# Ensure Redis is running and accessible
redis-cli -h localhost -p 6379 ping  # Should return PONG
redis-cli -c -h localhost -p 7000 cluster nodes  # Should show nodes

# Check Python dependencies
pip install -r requirements.txt --upgrade
```

### Benchmark errors

```bash
# Ensure both standalone and cluster are running
docker ps --filter "name=redis"

# Should see:
# - redis-standalone
# - redis-cluster-node-1 through redis-cluster-node-6

# Clear any stale data
redis-cli -h localhost -p 6379 FLUSHALL
redis-cli -c -h localhost -p 7000 FLUSHALL
```

## Performance Tips

1. **Use hash tags** in cluster mode for same-request keys
2. **Tune chunk_size** based on your workload (64-512)
3. **Monitor slot distribution** - balanced is better
4. **Use connection pooling** - set `max_connections` appropriately
5. **Batch when possible** - group related operations
6. **Profile your workload** - measure before optimizing

## Integration with LMCache

To integrate this enhanced connector into LMCache:

1. **Copy connector files** to LMCache repository:
   ```bash
   cp lmcache/v1/storage_backend/connector/redis_*.py \
      /path/to/LMCache/lmcache/v1/storage_backend/connector/
   ```

2. **Update configuration** in your LMCache config:
   ```yaml
   remote_url: "redis://your-redis-host:6379"
   remote_serde: "naive"
   extra_config:
     redis_mode: "cluster"  # or "standalone"
     chunk_size: 256
   ```

3. **Test** with your workload:
   ```bash
   # Run LMCache with Redis backend
   python -m lmcache.server ...
   ```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Development

### Running locally

```bash
# Install dev dependencies
pip install -r requirements.txt
pip install pytest pytest-asyncio black isort mypy

# Format code
black lmcache/ tests/ bench/
isort lmcache/ tests/ bench/

# Type check
mypy lmcache/

# Run tests
pytest tests/ -v
```

### Adding new tests

1. Add test functions to `tests/test_redis_batching.py`
2. Follow existing patterns for async tests (`@pytest.mark.asyncio`)
3. Use fixtures for Redis clients
4. Clean up test data in teardown

### Benchmarking new scenarios

1. Add configuration to `bench/test_redis_connector.py`
2. Run benchmark: `python bench/test_redis_connector.py`
3. Analyze results in `bench/results_ab.csv`
4. Document findings in `RESULTS.md`

## License

Apache-2.0 (matching LMCache upstream)

## References

- [LMCache Documentation](https://docs.lmcache.ai)
- [Redis Cluster Specification](https://redis.io/docs/reference/cluster-spec/)
- [Redis Pipelining](https://redis.io/docs/manual/pipelining/)
- [Hash Tags for Cluster](https://redis.io/docs/reference/cluster-spec/#hash-tags)

## Acknowledgments

- LMCache team for the original implementation
- Valkey connector for batching patterns
- Redis community for excellent documentation

## Support

For issues, questions, or contributions:
- Open an issue on GitHub
- See [CLAUDE.md](CLAUDE.md) for implementation details
- Check [RESULTS.md](RESULTS.md) for performance analysis
