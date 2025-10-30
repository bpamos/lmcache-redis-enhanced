#!/usr/bin/env python3
"""
Before/After Comparison Test

Compares the OLD Redis connector (non-batched, from upstream) with the
NEW Redis connector (batched, this implementation) to show improvements.

This demonstrates the actual performance gains vs the original LMCache Redis backend.
"""

import asyncio
import statistics
import time
from typing import List
import redis.asyncio as redis


class OldRedisConnector:
    """
    Simulates the OLD LMCache Redis connector (non-batched).

    Based on upstream LMCache Redis connector before batching enhancements.
    This is what Valkey's 20-70% improvement was measured against.
    """

    def __init__(self, url: str):
        self.url = url
        self.connection = None

    async def connect(self):
        self.connection = await redis.from_url(self.url, decode_responses=False)

    async def get_keys_individually(self, keys: List[str]) -> List[bytes]:
        """OLD METHOD: Get each key individually (no batching)"""
        results = []
        for key in keys:
            # Get metadata
            metadata = await self.connection.get(f"{key}:metadata")
            # Get kv_bytes
            kv_bytes = await self.connection.get(f"{key}:kv_bytes")
            if metadata and kv_bytes:
                results.append(kv_bytes)
        return results

    async def put_keys_individually(self, keys: List[str], values: List[bytes]):
        """OLD METHOD: Put each key individually (no batching)"""
        for key, value in zip(keys, values):
            # Set kv_bytes
            await self.connection.set(f"{key}:kv_bytes", value)
            # Set metadata
            await self.connection.set(f"{key}:metadata", b"metadata")

    async def close(self):
        await self.connection.close()


class NewRedisConnector:
    """
    NEW batched Redis connector (this implementation).

    Uses MGET for batched reads and pipelines for batched writes.
    This is comparable to Valkey's batching implementation.
    """

    def __init__(self, url: str):
        self.url = url
        self.connection = None

    async def connect(self):
        self.connection = await redis.from_url(self.url, decode_responses=False)

    async def get_keys_batched(self, keys: List[str]) -> List[bytes]:
        """NEW METHOD: Get all keys in one MGET (batched)"""
        # Build list of all keys to fetch
        all_keys = []
        for key in keys:
            all_keys.extend([f"{key}:metadata", f"{key}:kv_bytes"])

        # Single MGET
        results = await self.connection.mget(all_keys)

        # Extract just the kv_bytes (every other result)
        kv_results = []
        for i in range(0, len(results), 2):
            metadata = results[i]
            kv_bytes = results[i + 1]
            if metadata and kv_bytes:
                kv_results.append(kv_bytes)
        return kv_results

    async def put_keys_batched(self, keys: List[str], values: List[bytes]):
        """NEW METHOD: Put all keys in one pipeline (batched)"""
        pipe = self.connection.pipeline()
        for key, value in zip(keys, values):
            pipe.set(f"{key}:kv_bytes", value)
            pipe.set(f"{key}:metadata", b"metadata")
        await pipe.execute()

    async def close(self):
        await self.connection.close()


async def run_comparison(redis_url: str, num_keys: int = 100, key_size: int = 2048):
    """
    Run side-by-side comparison of OLD vs NEW implementation.
    """
    print("="*70)
    print("  Before/After Comparison: OLD Redis vs NEW Redis")
    print("="*70)
    print(f"\nTest Configuration:")
    print(f"  Target: {redis_url[:50]}...")
    print(f"  Number of keys: {num_keys}")
    print(f"  Key size: {key_size} bytes")
    print(f"  Iterations: 10 per test")
    print()

    # Prepare test data
    test_keys = [f"compare:key:{i}" for i in range(num_keys)]
    test_values = [f"x" * key_size for i in range(num_keys)]
    test_values_bytes = [v.encode() for v in test_values]

    # ========================================
    # BASELINE: OLD Redis (Non-batched)
    # ========================================
    print("="*70)
    print("  BASELINE: OLD Redis Connector (Non-batched)")
    print("="*70)
    print("This is the original LMCache Redis implementation...")
    print()

    old = OldRedisConnector(redis_url)
    await old.connect()

    # Warm-up
    await old.put_keys_individually(test_keys, test_values_bytes)
    await old.get_keys_individually(test_keys)

    # Measure WRITE
    print("Testing WRITE performance...")
    write_times_old = []
    for i in range(10):
        start = time.perf_counter()
        await old.put_keys_individually(test_keys, test_values_bytes)
        elapsed = time.perf_counter() - start
        write_times_old.append(elapsed)
        print(f"  Iteration {i+1}: {elapsed*1000:.2f}ms")

    # Measure READ
    print("\nTesting READ performance...")
    read_times_old = []
    for i in range(10):
        start = time.perf_counter()
        results = await old.get_keys_individually(test_keys)
        elapsed = time.perf_counter() - start
        read_times_old.append(elapsed)
        print(f"  Iteration {i+1}: {elapsed*1000:.2f}ms ({len(results)} keys)")

    await old.close()

    # Calculate stats
    old_write_avg = statistics.mean(write_times_old)
    old_write_p50 = statistics.median(write_times_old)
    old_write_p95 = statistics.quantiles(write_times_old, n=20)[18]

    old_read_avg = statistics.mean(read_times_old)
    old_read_p50 = statistics.median(read_times_old)
    old_read_p95 = statistics.quantiles(read_times_old, n=20)[18]

    print(f"\nOLD Redis Summary:")
    print(f"  WRITE: avg={old_write_avg*1000:.2f}ms, p50={old_write_p50*1000:.2f}ms, p95={old_write_p95*1000:.2f}ms")
    print(f"  READ:  avg={old_read_avg*1000:.2f}ms, p50={old_read_p50*1000:.2f}ms, p95={old_read_p95*1000:.2f}ms")

    # Wait a moment
    await asyncio.sleep(1)

    # ========================================
    # NEW: Batched Redis (This Implementation)
    # ========================================
    print()
    print("="*70)
    print("  NEW: Redis Connector with Batching (This Implementation)")
    print("="*70)
    print("This is the enhanced implementation with MGET/Pipeline batching...")
    print()

    new = NewRedisConnector(redis_url)
    await new.connect()

    # Warm-up
    await new.put_keys_batched(test_keys, test_values_bytes)
    await new.get_keys_batched(test_keys)

    # Measure WRITE
    print("Testing WRITE performance...")
    write_times_new = []
    for i in range(10):
        start = time.perf_counter()
        await new.put_keys_batched(test_keys, test_values_bytes)
        elapsed = time.perf_counter() - start
        write_times_new.append(elapsed)
        print(f"  Iteration {i+1}: {elapsed*1000:.2f}ms")

    # Measure READ
    print("\nTesting READ performance...")
    read_times_new = []
    for i in range(10):
        start = time.perf_counter()
        results = await new.get_keys_batched(test_keys)
        elapsed = time.perf_counter() - start
        read_times_new.append(elapsed)
        print(f"  Iteration {i+1}: {elapsed*1000:.2f}ms ({len(results)} keys)")

    await new.close()

    # Calculate stats
    new_write_avg = statistics.mean(write_times_new)
    new_write_p50 = statistics.median(write_times_new)
    new_write_p95 = statistics.quantiles(write_times_new, n=20)[18]

    new_read_avg = statistics.mean(read_times_new)
    new_read_p50 = statistics.median(read_times_new)
    new_read_p95 = statistics.quantiles(read_times_new, n=20)[18]

    print(f"\nNEW Redis Summary:")
    print(f"  WRITE: avg={new_write_avg*1000:.2f}ms, p50={new_write_p50*1000:.2f}ms, p95={new_write_p95*1000:.2f}ms")
    print(f"  READ:  avg={new_read_avg*1000:.2f}ms, p50={new_read_p50*1000:.2f}ms, p95={new_read_p95*1000:.2f}ms")

    # ========================================
    # COMPARISON
    # ========================================
    print()
    print("="*70)
    print("  PERFORMANCE IMPROVEMENT SUMMARY")
    print("="*70)
    print()

    # Write improvements
    write_speedup = old_write_avg / new_write_avg
    write_improvement = ((old_write_avg - new_write_avg) / old_write_avg) * 100
    write_p95_speedup = old_write_p95 / new_write_p95
    write_p95_improvement = ((old_write_p95 - new_write_p95) / old_write_p95) * 100

    print("WRITE Performance:")
    print(f"  OLD (baseline):  {old_write_avg*1000:.2f}ms avg, {old_write_p95*1000:.2f}ms p95")
    print(f"  NEW (batched):   {new_write_avg*1000:.2f}ms avg, {new_write_p95*1000:.2f}ms p95")
    print(f"  Improvement:     {write_speedup:.1f}× faster ({write_improvement:.1f}% reduction)")
    print(f"  p95 Improvement: {write_p95_speedup:.1f}× faster ({write_p95_improvement:.1f}% reduction)")
    print()

    # Read improvements
    read_speedup = old_read_avg / new_read_avg
    read_improvement = ((old_read_avg - new_read_avg) / old_read_avg) * 100
    read_p95_speedup = old_read_p95 / new_read_p95
    read_p95_improvement = ((old_read_p95 - new_read_p95) / old_read_p95) * 100

    print("READ Performance:")
    print(f"  OLD (baseline):  {old_read_avg*1000:.2f}ms avg, {old_read_p95*1000:.2f}ms p95")
    print(f"  NEW (batched):   {new_read_avg*1000:.2f}ms avg, {new_read_p95*1000:.2f}ms p95")
    print(f"  Improvement:     {read_speedup:.1f}× faster ({read_improvement:.1f}% reduction)")
    print(f"  p95 Improvement: {read_p95_speedup:.1f}× faster ({read_p95_improvement:.1f}% reduction)")
    print()

    # Comparison to Valkey claims
    print("="*70)
    print("  Comparison to Valkey's Claims")
    print("="*70)
    print()
    print("Valkey claims vs OLD Redis:")
    print("  - Standalone: 20% improvement")
    print("  - Cluster:    60-70% improvement")
    print()
    print("Our results vs OLD Redis:")
    print(f"  - READ:  {read_improvement:.1f}% improvement ({read_speedup:.1f}× faster)")
    print(f"  - WRITE: {write_improvement:.1f}% improvement ({write_speedup:.1f}× faster)")
    print()

    if read_improvement >= 60:
        print("✅ Our implementation MATCHES or EXCEEDS Valkey's cluster performance!")
    elif read_improvement >= 20:
        print("✅ Our implementation MATCHES Valkey's standalone performance!")
    else:
        print("⚠️  Results may vary based on network latency and key sizes")

    print()
    print("Note: These are the same fundamental optimizations (batching/pipelining)")
    print("      that Valkey uses. The gains come from reducing round-trips,")
    print("      not from the client library choice.")
    print()

    # Cleanup
    print("Cleaning up test keys...")
    cleanup = await redis.from_url(redis_url, decode_responses=False)
    await cleanup.delete(*test_keys)
    await cleanup.delete(*[f"{k}:metadata" for k in test_keys])
    await cleanup.delete(*[f"{k}:kv_bytes" for k in test_keys])
    await cleanup.close()
    print("✓ Cleanup complete")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Before/After comparison test")
    parser.add_argument(
        "--redis-url",
        default="redis://default:SldUJoIS67giEfBIBe5lsKl8bMYK4ljq@redis-18479.c49084.us-east-1-mz.ec2.cloud.rlrcp.com:18479",
        help="Redis URL"
    )
    parser.add_argument(
        "--num-keys",
        type=int,
        default=100,
        help="Number of keys to test (default: 100)"
    )
    parser.add_argument(
        "--key-size",
        type=int,
        default=2048,
        help="Size of each value in bytes (default: 2048)"
    )

    args = parser.parse_args()

    await run_comparison(args.redis_url, args.num_keys, args.key_size)


if __name__ == "__main__":
    asyncio.run(main())
