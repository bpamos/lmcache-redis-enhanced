#!/usr/bin/env python3
"""
Verification script for Redis setup.

Checks:
- Python dependencies installed
- Redis standalone connectivity
- Redis cluster connectivity
- Basic operations (SET/GET/MGET)
"""

import asyncio
import sys


def print_header(text):
    """Print section header"""
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def print_status(check, passed, details=""):
    """Print check status"""
    status = "✓" if passed else "✗"
    status_text = "PASS" if passed else "FAIL"
    print(f"  [{status}] {check}: {status_text}")
    if details:
        print(f"      {details}")


async def check_dependencies():
    """Check required Python packages"""
    print_header("Checking Dependencies")

    checks = []

    # Check redis
    try:
        import redis.asyncio as redis
        from redis.asyncio.cluster import RedisCluster

        version = redis.__version__
        checks.append(
            ("redis[asyncio]", True, f"version {version}")
        )
    except ImportError as e:
        checks.append(("redis[asyncio]", False, str(e)))

    # Check pytest
    try:
        import pytest

        version = pytest.__version__
        checks.append(("pytest", True, f"version {version}"))
    except ImportError as e:
        checks.append(("pytest", False, str(e)))

    for check, passed, details in checks:
        print_status(check, passed, details)

    return all(p for _, p, _ in checks)


async def check_standalone():
    """Check standalone Redis connectivity"""
    print_header("Checking Standalone Redis (localhost:6379)")

    try:
        import redis.asyncio as redis

        client = await redis.Redis(
            host="localhost", port=6379, decode_responses=False
        )

        # Ping
        pong = await client.ping()
        print_status("PING", pong, "connection successful")

        # SET
        await client.set("verify:test", b"hello")
        print_status("SET", True, "write successful")

        # GET
        value = await client.get("verify:test")
        print_status("GET", value == b"hello", f"read successful: {value}")

        # MGET
        await client.set("verify:key1", b"value1")
        await client.set("verify:key2", b"value2")
        results = await client.mget(["verify:key1", "verify:key2"])
        mget_ok = results == [b"value1", b"value2"]
        print_status("MGET", mget_ok, f"batch read successful: {results}")

        # Pipeline
        pipe = client.pipeline()
        pipe.set("verify:pipe1", b"pval1")
        pipe.set("verify:pipe2", b"pval2")
        await pipe.execute()
        print_status("Pipeline", True, "pipelined writes successful")

        # Cleanup
        await client.delete(
            "verify:test",
            "verify:key1",
            "verify:key2",
            "verify:pipe1",
            "verify:pipe2",
        )

        await client.close()
        return True

    except Exception as e:
        print_status("Standalone", False, f"Error: {e}")
        return False


async def check_cluster():
    """Check Redis Cluster connectivity"""
    print_header("Checking Redis Cluster (localhost:7000-7002)")

    try:
        from redis.asyncio.cluster import ClusterNode, RedisCluster

        startup_nodes = [
            ClusterNode("localhost", 7000),
            ClusterNode("localhost", 7001),
            ClusterNode("localhost", 7002),
        ]

        cluster = await RedisCluster(
            startup_nodes=startup_nodes, decode_responses=False
        )

        # Ping
        pong = await cluster.ping()
        print_status("PING", pong, "cluster connection successful")

        # Check cluster info
        info = await cluster.cluster_info()
        cluster_ok = b"cluster_state:ok" in info
        print_status("Cluster State", cluster_ok, "cluster is healthy")

        # SET with hash tag
        key = "{verify:cluster}:test"
        await cluster.set(key, b"cluster_value")
        print_status("SET (hash tag)", True, f"write successful: {key}")

        # GET
        value = await cluster.get(key)
        print_status(
            "GET (hash tag)", value == b"cluster_value", f"read successful: {value}"
        )

        # MGET with hash tags (same slot)
        key1 = "{verify:cluster}:key1"
        key2 = "{verify:cluster}:key2"
        await cluster.set(key1, b"value1")
        await cluster.set(key2, b"value2")
        results = await cluster.mget([key1, key2])
        mget_ok = results == [b"value1", b"value2"]
        print_status("MGET (hash tags)", mget_ok, f"batch read successful: {results}")

        # Pipeline (same slot)
        pipe = cluster.pipeline()
        pipe.set("{verify:cluster}:pipe1", b"pval1")
        pipe.set("{verify:cluster}:pipe2", b"pval2")
        await pipe.execute()
        print_status("Pipeline (hash tags)", True, "pipelined writes successful")

        # Verify slot consistency
        from redis.cluster import RedisClusterCommands

        metadata_key = "{verify:cluster}:metadata"
        kv_key = "{verify:cluster}:kv_bytes"
        slot_m = RedisClusterCommands.CLUSTER_KEYSLOT(metadata_key)
        slot_k = RedisClusterCommands.CLUSTER_KEYSLOT(kv_key)
        print_status(
            "Hash tag slot consistency",
            slot_m == slot_k,
            f"metadata slot={slot_m}, kv slot={slot_k}",
        )

        # Cleanup
        await cluster.delete(
            key,
            key1,
            key2,
            "{verify:cluster}:pipe1",
            "{verify:cluster}:pipe2",
        )

        await cluster.close()
        return True

    except Exception as e:
        print_status("Cluster", False, f"Error: {e}")
        print("\nNote: If cluster is not ready, wait a few seconds and try again.")
        print("Check cluster status: docker logs redis-cluster-init")
        return False


async def check_performance():
    """Quick performance check"""
    print_header("Quick Performance Check")

    try:
        import time

        import redis.asyncio as redis

        client = await redis.Redis(host="localhost", port=6379, decode_responses=False)

        # Individual GETs (baseline)
        num_keys = 100
        for i in range(num_keys):
            await client.set(f"perf:key:{i}", b"x" * 1024)

        start = time.perf_counter()
        for i in range(num_keys):
            await client.get(f"perf:key:{i}")
        baseline_time = time.perf_counter() - start

        # Batched MGET
        keys = [f"perf:key:{i}" for i in range(num_keys)]
        start = time.perf_counter()
        await client.mget(keys)
        batched_time = time.perf_counter() - start

        speedup = baseline_time / batched_time if batched_time > 0 else 0

        print_status(
            f"Individual GETs ({num_keys} keys)",
            True,
            f"{baseline_time*1000:.2f}ms",
        )
        print_status(f"Batched MGET ({num_keys} keys)", True, f"{batched_time*1000:.2f}ms")
        print_status("Speedup", speedup > 1, f"{speedup:.1f}×")

        # Cleanup
        await client.delete(*keys)
        await client.close()

        return True

    except Exception as e:
        print_status("Performance", False, f"Error: {e}")
        return False


async def main():
    """Run all checks"""
    print_header("Redis Setup Verification")
    print("This script verifies your Redis setup and dependencies.\n")

    results = []

    # Dependencies
    deps_ok = await check_dependencies()
    results.append(("Dependencies", deps_ok))

    if not deps_ok:
        print("\n⚠️  Missing dependencies. Install with:")
        print("   pip install -r requirements.txt")
        sys.exit(1)

    # Standalone
    standalone_ok = await check_standalone()
    results.append(("Standalone Redis", standalone_ok))

    # Cluster
    cluster_ok = await check_cluster()
    results.append(("Redis Cluster", cluster_ok))

    # Performance
    if standalone_ok:
        perf_ok = await check_performance()
        results.append(("Performance", perf_ok))

    # Summary
    print_header("Summary")
    all_passed = all(passed for _, passed in results)

    for check, passed in results:
        print_status(check, passed)

    if all_passed:
        print("\n✓ All checks passed! Your setup is ready.")
        print("\nNext steps:")
        print("  1. Run tests: pytest tests/ -v")
        print("  2. Run benchmarks: python bench/test_redis_connector.py")
        sys.exit(0)
    else:
        print("\n✗ Some checks failed. Please review the errors above.")
        print("\nTroubleshooting:")
        print("  1. Ensure Docker is running: docker ps")
        print("  2. Start Redis: docker-compose up -d")
        print("  3. Wait for cluster: docker logs redis-cluster-init")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
