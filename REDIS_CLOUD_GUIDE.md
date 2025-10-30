# Redis Cloud Testing Guide

This guide shows you how to test the Redis batching implementation with Redis Cloud, a managed Redis service.

## Table of Contents

- [Why Redis Cloud?](#why-redis-cloud)
- [Quick Start](#quick-start)
- [Setup Instructions](#setup-instructions)
- [Configuration Examples](#configuration-examples)
- [Running Tests](#running-tests)
- [Troubleshooting](#troubleshooting)

---

## Why Redis Cloud?

Redis Cloud is useful for:
- **Production-like testing** - Test against a real cloud instance
- **No local setup** - No need for Docker or local Redis
- **Cluster testing** - Easy access to Redis Cluster configurations
- **Performance baselines** - Measure real-world network latency improvements

---

## Quick Start

### 1. Get Redis Cloud Credentials

Sign up for a free account at [Redis Cloud](https://redis.com/try-free/)

After creating a database, you'll get a connection string like:
```
rediss://default:password@redis-12345.c123.us-east-1-1.ec2.cloud.redislabs.com:12345
```

### 2. Set Environment Variable

```bash
export REDIS_CLOUD_URL='rediss://default:YOUR_PASSWORD@YOUR_HOST:PORT'
```

For cluster mode:
```bash
export REDIS_CLOUD_URL='rediss://default:YOUR_PASSWORD@node1:port,node2:port,node3:port'
export REDIS_CLOUD_CLUSTER=true
```

### 3. Run Tests

```bash
# Simple end-to-end test
python test_simple.py --redis-cloud

# Unit tests
pytest tests/test_redis_batching.py --redis-url=$REDIS_CLOUD_URL -v

# Benchmarks
python bench/test_redis_connector.py --redis-url=$REDIS_CLOUD_URL
```

---

## Setup Instructions

### A. Redis Cloud Standalone

#### 1. Create Database

1. Go to [Redis Cloud Console](https://app.redislabs.com/)
2. Click "New Database"
3. Select:
   - **Type:** Redis Stack (or Redis)
   - **Region:** Choose closest to you
   - **Plan:** Free (for testing)
4. Create database and wait for it to be ready

#### 2. Get Connection Details

Click on your database to see:
- **Public endpoint:** `redis-12345.c123.us-east-1-1.ec2.cloud.redislabs.com:12345`
- **Default user password:** (click "eye" icon to reveal)

Note: Redis Cloud uses TLS by default (`rediss://`)

####  3. Test Connection

```bash
# Set env var
export REDIS_CLOUD_URL='rediss://default:YOUR_PASSWORD@YOUR_ENDPOINT'

# Test connection
redis-cli -u $REDIS_CLOUD_URL PING
# Should return: PONG
```

### B. Redis Cloud Cluster

#### 1. Create Cluster Database

1. In Redis Cloud Console, click "New Database"
2. Select:
   - **Type:** Redis
   - **Clustering:** Enabled
   - **Shards:** 3 (or more)
   - **Region:** Choose closest to you
4. Create and wait for ready

#### 2. Get Cluster Endpoints

You'll get multiple endpoints:
```
node1.cloud.redislabs.com:10000
node2.cloud.redislabs.com:10001
node3.cloud.redislabs.com:10002
```

#### 3. Configure

```bash
export REDIS_CLOUD_URL='rediss://default:PASSWORD@node1:10000,node2:10001,node3:10002'
export REDIS_CLOUD_CLUSTER=true
```

---

## Configuration Examples

### Standalone Mode

#### Environment Variables

```bash
export REDIS_CLOUD_URL='rediss://default:abc123@redis-12345.cloud.redislabs.com:12345'
```

#### LMCache Config (YAML)

```yaml
remote_url: "rediss://default:abc123@redis-12345.cloud.redislabs.com:12345"
remote_serde: "naive"
extra_config:
  redis_mode: "standalone"
  use_tls: true
  chunk_size: 256
  max_connections: 50  # Lower for cloud
  redis_username: "default"
  redis_password: "abc123"
```

#### Python Code

```python
import asyncio
import redis.asyncio as redis

async def test_cloud():
    client = await redis.from_url(
        "rediss://default:abc123@redis-12345.cloud.redislabs.com:12345",
        decode_responses=False,
        ssl=True,
    )

    # Test connection
    pong = await client.ping()
    print(f"Connected: {pong}")

    # Test batching
    await client.set("test:key", b"value")
    result = await client.get("test:key")
    print(f"Value: {result}")

    await client.close()

asyncio.run(test_cloud())
```

### Cluster Mode

#### Environment Variables

```bash
export REDIS_CLOUD_URL='rediss://default:abc123@node1.cloud.redislabs.com:10000,node2.cloud.redislabs.com:10001,node3.cloud.redislabs.com:10002'
export REDIS_CLOUD_CLUSTER=true
```

#### LMCache Config (YAML)

```yaml
remote_url: "rediss://default:abc123@node1.cloud.redislabs.com:10000,node2.cloud.redislabs.com:10001,node3.cloud.redislabs.com:10002"
remote_serde: "naive"
extra_config:
  redis_mode: "cluster"
  use_tls: true
  chunk_size: 256
  max_connections: 50
  redis_username: "default"
  redis_password: "abc123"
```

#### Python Code

```python
import asyncio
from redis.asyncio.cluster import ClusterNode, RedisCluster

async def test_cloud_cluster():
    nodes = [
        ClusterNode("node1.cloud.redislabs.com", 10000),
        ClusterNode("node2.cloud.redislabs.com", 10001),
        ClusterNode("node3.cloud.redislabs.com", 10002),
    ]

    cluster = await RedisCluster(
        startup_nodes=nodes,
        username="default",
        password="abc123",
        decode_responses=False,
        ssl=True,
    )

    # Test with hash tags
    await cluster.set("{test}:key1", b"value1")
    await cluster.set("{test}:key2", b"value2")

    results = await cluster.mget(["{test}:key1", "{test}:key2"])
    print(f"Batched results: {results}")

    await cluster.close()

asyncio.run(test_cloud_cluster())
```

---

## Running Tests

### 1. Simple End-to-End Test

This runs 7 comprehensive tests on your Redis Cloud instance:

```bash
python test_simple.py --redis-cloud
```

**Expected output:**
```
==========================================================
  Redis Batching Implementation Test
==========================================================

Target: rediss://default:***@redis-12345.cloud.redislabs.com:12345
Mode: Standalone
TLS: Enabled

Step 1: Testing Connection
  ✓ Connection successful (PING → PONG)

Step 2: Testing Basic Operations (SET/GET)
  ✓ SET test:simple:key = b'Hello, Redis!'
  ✓ GET test:simple:key = b'Hello, Redis!' (correct)
  ℹ Cleanup successful

...

Step 7: Testing Pipelined Writes
  ✓ Pipelined 50 SET operations
  ✓ All pipelined writes correct ✓
  ℹ Cleanup successful

==========================================================
  Test Summary
==========================================================

  ✓ Connection
  ✓ Basic Operations
  ✓ MGET Batching
  ✓ Order Preservation
  ✓ Missing Keys
  ✓ Performance
  ✓ Pipelined Writes

Results: 7/7 tests passed

✓ All tests passed!

Your Redis batching implementation is working correctly.
```

### 2. Unit Tests with Pytest

```bash
# All tests
pytest tests/test_redis_batching.py -v --redis-url=$REDIS_CLOUD_URL

# Specific test class
pytest tests/test_redis_batching.py::TestHashTagGeneration -v

# With detailed output
pytest tests/test_redis_batching.py -v -s
```

### 3. Benchmarks

```bash
# Full benchmark suite
python bench/test_redis_connector.py --redis-url=$REDIS_CLOUD_URL

# Standalone only
python bench/test_redis_connector.py \
  --redis-url=$REDIS_CLOUD_URL \
  --standalone

# Cluster only
python bench/test_redis_connector.py \
  --redis-url=$REDIS_CLOUD_URL \
  --cluster
```

**Example benchmark output:**
```
Running standalone benchmark...
  Batching: True
  Hash tags: False
  Key size: 2048B
  Batch size: 128
  Writing keys...
  Reading keys...
  ✓ p50=12.34ms p95=23.45ms p99=34.56ms

Average p95 improvement: 78.2%
Baseline avg p95: 98.76ms
Batched avg p95: 21.54ms
```

### 4. Custom Test Script

```python
#!/usr/bin/env python3
import asyncio
import os
import redis.asyncio as redis

async def test_redis_cloud():
    url = os.environ.get("REDIS_CLOUD_URL")
    if not url:
        print("Set REDIS_CLOUD_URL first")
        return

    print(f"Connecting to: {url[:30]}...")

    client = await redis.from_url(url, decode_responses=False, ssl=True)

    # Test 1: Simple SET/GET
    await client.set("test:1", b"Hello Cloud")
    val = await client.get("test:1")
    print(f"✓ SET/GET: {val}")

    # Test 2: Batched MGET
    for i in range(10):
        await client.set(f"batch:key:{i}", f"value_{i}".encode())

    keys = [f"batch:key:{i}" for i in range(10)]
    results = await client.mget(keys)
    print(f"✓ MGET: Retrieved {len(results)} values")

    # Test 3: Pipeline
    pipe = client.pipeline()
    for i in range(5):
        pipe.set(f"pipe:key:{i}", f"val_{i}".encode())
    await pipe.execute()
    print(f"✓ Pipeline: Wrote 5 keys")

    # Cleanup
    await client.delete("test:1", *keys, *[f"pipe:key:{i}" for i in range(5)])
    await client.close()

    print("\n✓ All tests passed!")

if __name__ == "__main__":
    asyncio.run(test_redis_cloud())
```

Save as `test_cloud_custom.py` and run:
```bash
python test_cloud_custom.py
```

---

## Troubleshooting

### Issue: Connection Refused

**Error:**
```
ConnectionRefusedError: [Errno 111] Connection refused
```

**Solutions:**
1. Check URL format includes `rediss://` (with double 's' for TLS)
2. Verify endpoint is correct (copy from Redis Cloud console)
3. Check firewall/network allows outbound HTTPS (port 443) and Redis (custom ports)

### Issue: Authentication Failed

**Error:**
```
redis.exceptions.AuthenticationError: invalid password
```

**Solutions:**
1. Verify password is correct (copy from Redis Cloud console)
2. Check username is `default` (or your custom user)
3. URL format: `rediss://username:password@host:port`
4. Password may contain special chars - use URL encoding:
   ```python
   from urllib.parse import quote
   password = quote("my!pass@word#", safe='')
   url = f"rediss://default:{password}@host:port"
   ```

### Issue: SSL/TLS Errors

**Error:**
```
ssl.SSLError: certificate verify failed
```

**Solutions:**
1. Ensure using `rediss://` (not `redis://`)
2. Update redis-py: `pip install --upgrade redis`
3. Set `ssl_cert_reqs=None` in code (for testing only):
   ```python
   client = await redis.from_url(url, ssl_cert_reqs=None)
   ```

###  Issue: Timeout Errors

**Error:**
```
redis.exceptions.TimeoutError: Timeout reading from socket
```

**Solutions:**
1. Check network latency to cloud region
2. Increase timeout:
   ```python
   client = await redis.from_url(url, socket_timeout=10.0)
   ```
3. Lower `max_connections` in config (cloud limits may be lower)
4. Check Redis Cloud dashboard for connection limits

### Issue: Cluster CROSSSLOT Errors

**Error:**
```
redis.exceptions.ResponseError: CROSSSLOT Keys in request don't hash to the same slot
```

**Solutions:**
1. Use hash tags: `{trace_id}:key` instead of `trace_id:key`
2. Ensure all related keys have same hash tag:
   ```python
   # Good
   "{user:123}:metadata"
   "{user:123}:kv_bytes"

   # Bad
   "user:123:metadata"
   "user:123:kv_bytes"
   ```
3. For MGET/pipeline, group keys by slot first

### Issue: Rate Limiting

**Error:**
```
Too many connections
```

**Solutions:**
1. Reduce `max_connections` to 20-50 for cloud
2. Add backoff/retry logic
3. Check Redis Cloud plan limits
4. Upgrade plan if hitting limits consistently

---

## Performance Expectations

### Latency with Redis Cloud

Redis Cloud adds network latency compared to local Docker:

| Scenario | Local (Docker) | Same Region Cloud | Cross Region Cloud |
|----------|----------------|-------------------|-------------------|
| Single GET | <1ms | 5-15ms | 50-150ms |
| MGET (100 keys) | 2-5ms | 10-30ms | 80-200ms |
| Baseline (100 individual GETs) | 50-100ms | 500-1500ms | 5000-15000ms |

### Batching Benefits

The batching improvement is MORE dramatic with cloud due to higher baseline latency:

| Batch Size | Baseline | Batched | Improvement |
|------------|----------|---------|-------------|
| 64 keys | ~800ms | ~15ms | **98%** ↓ |
| 128 keys | ~1600ms | ~25ms | **98.4%** ↓ |
| 256 keys | ~3200ms | ~40ms | **98.8%** ↓ |

**Key insight:** Higher network latency makes batching even more critical!

---

## Best Practices

### 1. Connection Pooling

```yaml
extra_config:
  max_connections: 50  # Lower for cloud (vs 150 for local)
```

### 2. Timeouts

```python
client = await redis.from_url(
    url,
    socket_timeout=5.0,  # 5 second timeout
    socket_connect_timeout=5.0,
)
```

### 3. Retry Logic

```python
from redis.exceptions import ConnectionError, TimeoutError

async def get_with_retry(client, key, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await client.get(key)
        except (ConnectionError, TimeoutError) as e:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(0.1 * (2 ** attempt))  # Exponential backoff
```

### 4. Monitor Usage

- Check Redis Cloud dashboard for:
  - Connection count
  - Memory usage
  - Throughput
  - Latency metrics

### 5. Cost Optimization

- Use free tier for development/testing
- Upgrade only when needed for production
- Monitor data transfer costs
- Consider reserved instances for production

---

## Example: Full Integration Test

Here's a complete example testing all features against Redis Cloud:

```bash
#!/bin/bash
# test_cloud_full.sh

set -e

echo "Redis Cloud Full Integration Test"
echo "=================================="

# Check env var
if [ -z "$REDIS_CLOUD_URL" ]; then
    echo "Error: REDIS_CLOUD_URL not set"
    echo "Set with: export REDIS_CLOUD_URL='rediss://...'"
    exit 1
fi

echo "✓ REDIS_CLOUD_URL is set"
echo

# 1. Simple test
echo "Running simple end-to-end test..."
python test_simple.py --redis-cloud
echo

# 2. Unit tests
echo "Running unit tests..."
pytest tests/test_redis_batching.py -v --redis-url=$REDIS_CLOUD_URL -x
echo

# 3. Quick benchmark
echo "Running quick benchmark..."
python -c "
import asyncio
import time
import redis.asyncio as redis

async def bench():
    client = await redis.from_url('$REDIS_CLOUD_URL', decode_responses=False, ssl=True)

    # Write 100 keys
    for i in range(100):
        await client.set(f'bench:key:{i}', b'x' * 1024)

    # Baseline: individual GETs
    start = time.perf_counter()
    for i in range(100):
        await client.get(f'bench:key:{i}')
    baseline = time.perf_counter() - start

    # Batched: MGET
    keys = [f'bench:key:{i}' for i in range(100)]
    start = time.perf_counter()
    await client.mget(keys)
    batched = time.perf_counter() - start

    speedup = baseline / batched
    print(f'Baseline: {baseline*1000:.0f}ms')
    print(f'Batched: {batched*1000:.0f}ms')
    print(f'Speedup: {speedup:.1f}×')

    # Cleanup
    await client.delete(*keys)
    await client.close()

asyncio.run(bench())
"

echo
echo "=================================="
echo "✓ All tests passed!"
echo "Your Redis Cloud setup is working correctly."
```

Make it executable and run:
```bash
chmod +x test_cloud_full.sh
./test_cloud_full.sh
```

---

## Next Steps

1. ✅ Test with Redis Cloud standalone
2. ✅ Test with Redis Cloud cluster (if available)
3. ✅ Run benchmarks to measure real-world improvements
4. ✅ Integrate into your LMCache deployment
5. 📊 Monitor performance in production
6. 🚀 Optimize based on metrics

---

## Support

- **Redis Cloud Issues:** [Redis Support](https://redis.com/company/support/)
- **LMCache Redis Issues:** Open issue on GitHub
- **This Implementation:** See README.md

---

*Last updated: 2025-10-30*
