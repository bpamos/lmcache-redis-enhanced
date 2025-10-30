# Testing Guide

Complete guide to testing the Redis batching implementation.

## Quick Testing

### Simplest Test (Local Docker)

```bash
# Start Redis
docker-compose up -d

# Run simple test
python test_simple.py
```

**Output:** 7 tests with clear ✓/✗ indicators showing what works.

### Simplest Test (Redis Cloud)

```bash
# Set your Redis Cloud URL
export REDIS_CLOUD_URL='rediss://default:YOUR_PASSWORD@YOUR_HOST:PORT'

# Run simple test
python test_simple.py --redis-cloud
```

**Output:** Same 7 tests, but against your cloud instance.

---

## Test Options

### 1. Simple End-to-End Test (Recommended)

**What it tests:**
- ✓ Connection
- ✓ Basic SET/GET
- ✓ Batched MGET
- ✓ Order preservation
- ✓ Missing key handling
- ✓ Performance (baseline vs batched)
- ✓ Pipelined writes

**Run:**
```bash
# Local standalone
python test_simple.py

# Local cluster
python test_simple.py --cluster

# Redis Cloud
python test_simple.py --redis-cloud

# Custom Redis
python test_simple.py --redis-url redis://myhost:6379
```

**When to use:** First test to run. Quick verification everything works.

---

### 2. Unit Tests (Detailed)

**What it tests:**
- Hash tag generation
- Slot grouping
- MGET order preservation
- Pipeline correctness
- Standalone vs cluster parity

**Run:**
```bash
# All tests
pytest tests/test_redis_batching.py -v

# Specific test class
pytest tests/test_redis_batching.py::TestHashTagGeneration -v

# Stop on first failure
pytest tests/test_redis_batching.py -x

# With detailed output
pytest tests/test_redis_batching.py -v -s
```

**When to use:** After code changes, before submitting PR.

---

### 3. Benchmarks (Performance)

**What it tests:**
- Latency (p50/p95/p99)
- Throughput (ops/sec)
- Round-trips per request
- Baseline vs batched comparison

**Run:**
```bash
# Full benchmark suite
python bench/test_redis_connector.py

# Custom configuration
python bench/test_redis_connector.py \
  --key-size 2048 \
  --batch-size 128 \
  --num-iterations 100
```

**Output:** CSV file `bench/results_ab.csv` with detailed metrics.

**When to use:** Measuring performance improvements, tuning configuration.

---

### 4. Setup Verification

**What it tests:**
- Dependencies installed
- Redis connectivity (standalone + cluster)
- Basic operations
- Quick performance check

**Run:**
```bash
python verify_setup.py
```

**When to use:** After initial setup, troubleshooting issues.

---

## Testing Scenarios

### Scenario 1: "Does it work?"

```bash
python test_simple.py
```

✅ **Pass:** See 7/7 tests passed
✗ **Fail:** Review error messages, check Redis is running

---

### Scenario 2: "Is it correct?"

```bash
pytest tests/test_redis_batching.py -v
```

✅ **Pass:** All unit tests green
✗ **Fail:** Fix implementation, review test failures

---

### Scenario 3: "Is it faster?"

```bash
python test_simple.py
```

Look for "Step 6: Testing Performance" output:
```
Performance improvement: 5.2× faster (80.7% reduction)
```

✅ **Expected:** 3-10× speedup depending on batch size and network latency
⚠️ **Warning:** <2× may indicate issues

---

### Scenario 4: "Does it work with my Redis Cloud?"

```bash
export REDIS_CLOUD_URL='rediss://...'
python test_simple.py --redis-cloud
```

✅ **Pass:** All tests pass with cloud instance
✗ **Fail:** See [Redis Cloud Guide](REDIS_CLOUD_GUIDE.md) troubleshooting

---

## Test Environments

### Local Docker (Standalone)

```bash
# Start
docker-compose up -d

# Test
python test_simple.py

# Stop
docker-compose down
```

**Pros:** Fast, no external dependencies
**Cons:** Not production-like

---

### Local Docker (Cluster)

```bash
# Start
docker-compose up -d

# Wait for cluster init
docker logs redis-cluster-init

# Test
python test_simple.py --cluster

# Stop
docker-compose down
```

**Pros:** Tests cluster features locally
**Cons:** Takes longer to start

---

### Redis Cloud (Standalone)

```bash
export REDIS_CLOUD_URL='rediss://default:password@host:port'
python test_simple.py --redis-cloud
```

**Pros:** Production-like, no local setup
**Cons:** Slower due to network latency, may have rate limits

---

### Redis Cloud (Cluster)

```bash
export REDIS_CLOUD_URL='rediss://default:password@node1:port,node2:port,node3:port'
export REDIS_CLOUD_CLUSTER=true
python test_simple.py --redis-cloud
```

**Pros:** Tests real cluster behavior
**Cons:** Requires cluster plan, higher cost

---

## Interpreting Results

### test_simple.py Output

```
Step 1: Testing Connection
  ✓ Connection successful (PING → PONG)
```
✅ Good - Redis is accessible

```
Step 3: Testing Batched MGET
  ✓ Created 10 test keys
  ✓ MGET retrieved 10 values
  ✓ All values correct ✓
```
✅ Good - Batching works correctly

```
Step 6: Testing Performance (Baseline vs Batched)
  ℹ Baseline (individual GETs): 87.23ms
  ℹ Batched (single MGET): 15.42ms
  ✓ Performance improvement: 5.7× faster (82.3% reduction)
```
✅ Excellent - Significant speedup

---

### pytest Output

```
tests/test_redis_batching.py::TestHashTagGeneration::test_hash_tag_format PASSED
tests/test_redis_batching.py::TestHashTagGeneration::test_hash_tag_slot_consistency PASSED
```
✅ Good - Tests passing

```
tests/test_redis_batching.py::TestRedisStandaloneBatching::test_mget_order_preservation FAILED
```
✗ Problem - Order not preserved, check implementation

---

### Benchmark Output

```
Running standalone benchmark...
  Batching: True
  Key size: 2048B
  Batch size: 128
  ✓ p50=4.23ms p95=8.45ms p99=12.34ms

Average p95 improvement: 78.2%
```
✅ Excellent - ~80% latency reduction

---

## Common Issues

### Issue: "Connection refused"

```
python test_simple.py
```
```
ConnectionRefusedError: [Errno 111] Connection refused
```

**Fix:**
```bash
# Check Redis is running
docker ps | grep redis

# If not, start it
docker-compose up -d
```

---

### Issue: "Cluster not ready"

```
python test_simple.py --cluster
```
```
redis.exceptions.ClusterDownError: CLUSTERDOWN
```

**Fix:**
```bash
# Check cluster status
docker logs redis-cluster-init

# Wait for: [OK] All 16384 slots covered
```

---

### Issue: "Tests very slow"

```
python test_simple.py
# Takes > 30 seconds
```

**Possible causes:**
1. Using Redis Cloud with high latency → Expected
2. Local network issues → Check firewall
3. Redis under load → Restart: `docker-compose restart`

---

### Issue: "Some tests fail intermittently"

**Possible causes:**
1. Race conditions → Run again
2. Redis eviction → Increase memory
3. Network issues → Check stability

---

## Test Coverage Summary

| Feature | Simple Test | Unit Tests | Benchmarks |
|---------|-------------|-----------|-----------|
| Connection | ✓ | ✓ | ✓ |
| Basic ops | ✓ | ✓ | ✓ |
| MGET batching | ✓ | ✓ | ✓ |
| Order preservation | ✓ | ✓ | - |
| Missing keys | ✓ | ✓ | - |
| Hash tags | - | ✓ | - |
| Slot grouping | - | ✓ | - |
| Pipeline writes | ✓ | ✓ | ✓ |
| Performance | ✓ | - | ✓ |
| Cluster mode | ✓ | ✓ | ✓ |
| TLS/SSL | ✓ | - | ✓ |

---

## Next Steps

1. ✅ Run `python test_simple.py` - Verify basics work
2. ✅ Run `pytest tests/ -v` - Ensure correctness
3. ✅ Review performance output - Confirm improvements
4. 📊 Run benchmarks if tuning - Get detailed metrics
5. ☁️ Test with Redis Cloud - Validate production-like setup

---

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Redis Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      redis:
        image: redis:8-alpine
        ports:
          - 6379:6379

    steps:
      - uses: actions/checkout@v2

      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run simple test
        run: python test_simple.py

      - name: Run unit tests
        run: pytest tests/ -v

      - name: Run benchmarks
        run: python bench/test_redis_connector.py
```

---

## Questions?

- **Setup issues?** Run `python verify_setup.py`
- **Redis Cloud?** See [Redis Cloud Guide](REDIS_CLOUD_GUIDE.md)
- **Performance tuning?** See [RESULTS.md](RESULTS.md)
- **General help?** See [README.md](README.md)

---

*Last updated: 2025-10-30*
