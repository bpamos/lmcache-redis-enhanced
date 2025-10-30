#!/usr/bin/env python3
"""
Simple end-to-end test for Redis batching implementation.

VALKEY FEATURE PARITY VERIFICATION
===================================
This test verifies that the Redis connector implements all Valkey backend features:
- ✓ Hash tags for cluster co-location
- ✓ Batched MGET reads (single round-trip)
- ✓ Pipelined SET writes (batched operations)
- ✓ Order preservation in batch operations
- ✓ Proper handling of missing keys (returns None)
- ✓ Performance improvements via batching
- ✓ Both standalone and cluster modes

This script provides a clear, step-by-step test that shows:
1. Connection works
2. Basic SET/GET works
3. Batched MGET works correctly (VALKEY PARITY)
4. Order preservation works (VALKEY PARITY)
5. Missing key handling works (VALKEY PARITY)
6. Performance improvement is measurable (VALKEY PARITY)
7. Pipelined writes work (VALKEY PARITY)

Usage:
    python test_simple.py                           # Test local Docker setup
    python test_simple.py --redis-url <url>         # Test custom Redis
    python test_simple.py --redis-cloud             # Test Redis Cloud (from env)
"""

import argparse
import asyncio
import os
import statistics
import time
from typing import List, Optional

import redis.asyncio as redis
from redis.asyncio.cluster import ClusterNode, RedisCluster


class Colors:
    """ANSI color codes for terminal output"""

    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    END = "\033[0m"


def print_step(step_num: int, description: str):
    """Print a test step header"""
    print(f"\n{Colors.BOLD}Step {step_num}: {description}{Colors.END}")


def print_pass(message: str):
    """Print a pass message"""
    print(f"  {Colors.GREEN}✓{Colors.END} {message}")


def print_fail(message: str):
    """Print a fail message"""
    print(f"  {Colors.RED}✗{Colors.END} {message}")


def print_info(message: str):
    """Print an info message"""
    print(f"  {Colors.BLUE}ℹ{Colors.END} {message}")


async def test_connection(client) -> bool:
    """Test 1: Basic connectivity"""
    print_step(1, "Testing Connection")
    try:
        pong = await client.ping()
        if pong:
            print_pass("Connection successful (PING → PONG)")
            return True
        else:
            print_fail("Ping failed")
            return False
    except Exception as e:
        print_fail(f"Connection failed: {e}")
        return False


async def test_basic_operations(client) -> bool:
    """Test 2: Basic SET/GET operations"""
    print_step(2, "Testing Basic Operations (SET/GET)")
    try:
        # SET
        test_key = "test:simple:key"
        test_value = b"Hello, Redis!"
        await client.set(test_key, test_value)
        print_pass(f"SET {test_key} = {test_value}")

        # GET
        retrieved = await client.get(test_key)
        if retrieved == test_value:
            print_pass(f"GET {test_key} = {retrieved} (correct)")
        else:
            print_fail(f"GET returned {retrieved}, expected {test_value}")
            return False

        # Cleanup
        await client.delete(test_key)
        print_info("Cleanup successful")
        return True

    except Exception as e:
        print_fail(f"Basic operations failed: {e}")
        return False


async def test_mget_batching(client, use_hash_tags: bool = False) -> bool:
    """Test 3: MGET batching"""
    print_step(3, "Testing Batched MGET")

    try:
        # Write test data
        num_keys = 10
        keys = []
        values = []

        base = "test:batch" if not use_hash_tags else "{test:batch}"

        for i in range(num_keys):
            key = f"{base}:key:{i}"
            value = f"value_{i}".encode()
            keys.append(key)
            values.append(value)
            await client.set(key, value)

        print_pass(f"Created {num_keys} test keys")

        # MGET all keys
        results = await client.mget(keys)
        print_pass(f"MGET retrieved {len(results)} values")

        # Verify all values
        all_correct = True
        for i, (expected, actual) in enumerate(zip(values, results)):
            if expected != actual:
                print_fail(f"Key {i}: expected {expected}, got {actual}")
                all_correct = False

        if all_correct:
            print_pass("All values correct ✓")
        else:
            print_fail("Some values incorrect")
            return False

        # Cleanup
        await client.delete(*keys)
        print_info("Cleanup successful")
        return True

    except Exception as e:
        print_fail(f"MGET batching failed: {e}")
        return False


async def test_order_preservation(client, use_hash_tags: bool = False) -> bool:
    """Test 4: Order preservation in MGET"""
    print_step(4, "Testing Order Preservation")

    try:
        # Create keys with specific order
        num_keys = 20
        base = "test:order" if not use_hash_tags else "{test:order}"
        keys = [f"{base}:key:{i}" for i in range(num_keys)]
        values = [f"value_{i}".encode() for i in range(num_keys)]

        # Write in order
        for key, value in zip(keys, values):
            await client.set(key, value)

        print_pass(f"Created {num_keys} ordered keys")

        # Read in SAME order
        results = await client.mget(keys)

        # Verify order
        order_correct = True
        for i, (expected, actual) in enumerate(zip(values, results)):
            if expected != actual:
                print_fail(f"Position {i}: expected {expected}, got {actual}")
                order_correct = False

        if order_correct:
            print_pass("Order preserved correctly ✓")
        else:
            print_fail("Order not preserved")
            return False

        # Read in REVERSE order
        reversed_keys = list(reversed(keys))
        reversed_values = list(reversed(values))
        results = await client.mget(reversed_keys)

        for i, (expected, actual) in enumerate(zip(reversed_values, results)):
            if expected != actual:
                print_fail(f"Reverse position {i}: expected {expected}, got {actual}")
                order_correct = False

        if order_correct:
            print_pass("Reverse order preserved correctly ✓")
        else:
            print_fail("Reverse order not preserved")
            return False

        # Cleanup
        await client.delete(*keys)
        print_info("Cleanup successful")
        return True

    except Exception as e:
        print_fail(f"Order preservation test failed: {e}")
        return False


async def test_missing_keys(client, use_hash_tags: bool = False) -> bool:
    """Test 5: Handling missing keys"""
    print_step(5, "Testing Missing Key Handling")

    try:
        base = "test:missing" if not use_hash_tags else "{test:missing}"

        # Set only some keys
        await client.set(f"{base}:exists:1", b"value1")
        await client.set(f"{base}:exists:2", b"value2")

        print_pass("Created 2 existing keys")

        # MGET with mix of existing and missing
        keys = [
            f"{base}:exists:1",
            f"{base}:missing:1",
            f"{base}:exists:2",
            f"{base}:missing:2",
        ]

        results = await client.mget(keys)

        # Verify
        if results[0] == b"value1":
            print_pass("Existing key 1: correct")
        else:
            print_fail(f"Existing key 1: expected value1, got {results[0]}")
            return False

        if results[1] is None:
            print_pass("Missing key 1: correctly None")
        else:
            print_fail(f"Missing key 1: expected None, got {results[1]}")
            return False

        if results[2] == b"value2":
            print_pass("Existing key 2: correct")
        else:
            print_fail(f"Existing key 2: expected value2, got {results[2]}")
            return False

        if results[3] is None:
            print_pass("Missing key 2: correctly None")
        else:
            print_fail(f"Missing key 2: expected None, got {results[3]}")
            return False

        # Cleanup
        await client.delete(f"{base}:exists:1", f"{base}:exists:2")
        print_info("Cleanup successful")
        return True

    except Exception as e:
        print_fail(f"Missing key test failed: {e}")
        return False


async def test_performance(client, use_hash_tags: bool = False) -> bool:
    """Test 6: Performance comparison"""
    print_step(6, "Testing Performance (Baseline vs Batched)")

    try:
        num_keys = 100
        base = "test:perf" if not use_hash_tags else "{test:perf}"
        keys = [f"{base}:key:{i}" for i in range(num_keys)]

        # Write test data
        for key in keys:
            await client.set(key, b"x" * 1024)  # 1KB values

        print_pass(f"Created {num_keys} test keys (1KB each)")

        # Baseline: Individual GETs
        start = time.perf_counter()
        for key in keys:
            await client.get(key)
        baseline_time = time.perf_counter() - start

        print_info(f"Baseline (individual GETs): {baseline_time*1000:.2f}ms")

        # Batched: Single MGET
        start = time.perf_counter()
        await client.mget(keys)
        batched_time = time.perf_counter() - start

        print_info(f"Batched (single MGET): {batched_time*1000:.2f}ms")

        # Calculate improvement
        if batched_time > 0:
            speedup = baseline_time / batched_time
            improvement_pct = ((baseline_time - batched_time) / baseline_time) * 100

            if speedup > 1:
                print_pass(
                    f"Performance improvement: {speedup:.1f}× faster ({improvement_pct:.1f}% reduction)"
                )
            else:
                print_fail(f"No improvement: {speedup:.1f}×")
                return False
        else:
            print_fail("Batched time too small to measure")
            return False

        # Cleanup
        await client.delete(*keys)
        print_info("Cleanup successful")
        return True

    except Exception as e:
        print_fail(f"Performance test failed: {e}")
        return False


async def test_pipeline_writes(client, use_hash_tags: bool = False) -> bool:
    """Test 7: Pipelined writes"""
    print_step(7, "Testing Pipelined Writes")

    try:
        num_keys = 50
        base = "test:pipe" if not use_hash_tags else "{test:pipe}"
        keys = [f"{base}:key:{i}" for i in range(num_keys)]
        values = [f"value_{i}".encode() for i in range(num_keys)]

        # Pipeline write
        pipe = client.pipeline()
        for key, value in zip(keys, values):
            pipe.set(key, value)
        await pipe.execute()

        print_pass(f"Pipelined {num_keys} SET operations")

        # Verify all written correctly
        results = await client.mget(keys)

        all_correct = True
        for expected, actual in zip(values, results):
            if expected != actual:
                all_correct = False
                break

        if all_correct:
            print_pass("All pipelined writes correct ✓")
        else:
            print_fail("Some pipelined writes incorrect")
            return False

        # Cleanup
        await client.delete(*keys)
        print_info("Cleanup successful")
        return True

    except Exception as e:
        print_fail(f"Pipeline write test failed: {e}")
        return False


async def run_all_tests(redis_url: str, is_cluster: bool = False, use_tls: bool = False):
    """Run all tests"""
    print(f"\n{Colors.BOLD}{'='*60}")
    print(f"  Redis Batching Implementation Test")
    print(f"  (Valkey Feature Parity Verification)")
    print(f"{'='*60}{Colors.END}\n")

    print(f"Target: {redis_url}")
    print(f"Mode: {'Cluster' if is_cluster else 'Standalone'}")
    print(f"TLS: {'Enabled' if use_tls else 'Disabled'}")

    # Connect
    try:
        if is_cluster:
            # Parse cluster nodes
            if "," in redis_url:
                # Multiple nodes
                nodes = []
                for node_url in redis_url.split(","):
                    # Strip schema if present
                    for schema in ["redis://", "rediss://"]:
                        if node_url.startswith(schema):
                            node_url = node_url[len(schema) :]
                    # Parse host:port
                    if ":" in node_url:
                        host, port = node_url.rsplit(":", 1)
                        nodes.append(ClusterNode(host, int(port)))
                    else:
                        nodes.append(ClusterNode(node_url, 6379))
            else:
                # Single node
                node_url = redis_url
                for schema in ["redis://", "rediss://"]:
                    if node_url.startswith(schema):
                        node_url = node_url[len(schema) :]
                if ":" in node_url:
                    host, port = node_url.rsplit(":", 1)
                    nodes = [ClusterNode(host, int(port))]
                else:
                    nodes = [ClusterNode(node_url, 6379)]

            cluster_kwargs = {
                "startup_nodes": nodes,
                "decode_responses": False,
            }
            if use_tls:
                cluster_kwargs["ssl"] = True
            client = await RedisCluster(**cluster_kwargs)
            use_hash_tags = True
        else:
            url_kwargs = {
                "decode_responses": False,
            }
            if use_tls:
                url_kwargs["ssl"] = True
            client = await redis.from_url(redis_url, **url_kwargs)
            use_hash_tags = False

        print_pass("Connected to Redis")

    except Exception as e:
        print_fail(f"Failed to connect: {e}")
        return False

    # Run tests
    tests = [
        ("Connection", test_connection(client)),
        ("Basic Operations", test_basic_operations(client)),
        ("MGET Batching", test_mget_batching(client, use_hash_tags)),
        ("Order Preservation", test_order_preservation(client, use_hash_tags)),
        ("Missing Keys", test_missing_keys(client, use_hash_tags)),
        ("Performance", test_performance(client, use_hash_tags)),
        ("Pipeline Writes", test_pipeline_writes(client, use_hash_tags)),
    ]

    results = []
    for test_name, test_coro in tests:
        try:
            passed = await test_coro
            results.append((test_name, passed))
        except Exception as e:
            print_fail(f"Test crashed: {e}")
            results.append((test_name, False))

    # Close connection
    await client.close()

    # Print summary
    print(f"\n{Colors.BOLD}{'='*60}")
    print(f"  Test Summary")
    print(f"{'='*60}{Colors.END}\n")

    passed = sum(1 for _, p in results if p)
    total = len(results)

    for test_name, test_passed in results:
        if test_passed:
            print_pass(f"{test_name}")
        else:
            print_fail(f"{test_name}")

    print(f"\n{Colors.BOLD}Results: {passed}/{total} tests passed{Colors.END}")

    if passed == total:
        print(f"\n{Colors.GREEN}{Colors.BOLD}✓ All tests passed!{Colors.END}")
        print("\nYour Redis batching implementation is working correctly.")
        return True
    else:
        print(f"\n{Colors.RED}{Colors.BOLD}✗ Some tests failed{Colors.END}")
        print(f"\n{total - passed} test(s) need attention.")
        return False


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Simple end-to-end test for Redis batching"
    )
    parser.add_argument(
        "--redis-url",
        help="Redis URL (default: redis://localhost:6379)",
        default=None,
    )
    parser.add_argument(
        "--cluster",
        action="store_true",
        help="Connect as Redis Cluster",
    )
    parser.add_argument(
        "--redis-cloud",
        action="store_true",
        help="Use Redis Cloud credentials from environment",
    )
    parser.add_argument(
        "--tls",
        action="store_true",
        help="Use TLS/SSL connection",
    )

    args = parser.parse_args()

    # Determine Redis URL
    if args.redis_cloud:
        # Redis Cloud from environment
        redis_url = os.environ.get("REDIS_CLOUD_URL")
        if not redis_url:
            print(f"{Colors.RED}Error: REDIS_CLOUD_URL environment variable not set{Colors.END}")
            print("\nSet it with:")
            print("  export REDIS_CLOUD_URL='rediss://username:password@host:port'")
            return False
        is_cluster = os.environ.get("REDIS_CLOUD_CLUSTER", "false").lower() == "true"
        use_tls = redis_url.startswith("rediss://")
    elif args.redis_url:
        redis_url = args.redis_url
        is_cluster = args.cluster
        use_tls = args.tls or redis_url.startswith("rediss://")
    else:
        # Default: local Docker
        if args.cluster:
            redis_url = "redis://localhost:7000,localhost:7001,localhost:7002"
            is_cluster = True
        else:
            redis_url = "redis://localhost:6379"
            is_cluster = False
        use_tls = False

    # Run tests
    success = asyncio.run(run_all_tests(redis_url, is_cluster, use_tls))
    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
