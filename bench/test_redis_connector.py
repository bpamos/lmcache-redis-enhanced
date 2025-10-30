#!/usr/bin/env python3
"""
Benchmark script for Redis connector batching and pipelining.

Tests:
- Standalone vs Cluster mode
- Different key sizes (512B, 2KB, 8KB)
- Different batch sizes (32, 64, 128, 256 keys)
- Different chunk sizes (64, 128, 256)
- Measures p50, p95, p99 latencies and throughput
"""

import asyncio
import csv
import random
import statistics
import time
from dataclasses import dataclass
from typing import List, Optional

import redis.asyncio as redis
from redis.asyncio.cluster import ClusterNode, RedisCluster


@dataclass
class BenchmarkConfig:
    """Configuration for benchmark runs"""

    mode: str  # "standalone" or "cluster"
    host: str
    port: int
    cluster_nodes: Optional[List[tuple]] = None  # For cluster mode
    key_size: int = 2048  # Size of values in bytes
    num_keys: int = 1000  # Total keys to write
    batch_size: int = 128  # Keys per batch read
    chunk_size: int = 256  # Chunk size for batching
    num_iterations: int = 100  # Number of read batches to test
    use_batching: bool = True  # Use new batched implementation
    use_hash_tags: bool = True  # Use hash tags for cluster (slot co-location)


@dataclass
class BenchmarkResult:
    """Results from a benchmark run"""

    config: BenchmarkConfig
    write_latency_p50: float
    write_latency_p95: float
    write_latency_p99: float
    read_latency_p50: float
    read_latency_p95: float
    read_latency_p99: float
    read_ops_per_sec: float
    read_bytes_per_sec: float
    estimated_round_trips_per_request: float


class RedisStandaloneBenchmark:
    """Benchmark for standalone Redis"""

    def __init__(self, config: BenchmarkConfig):
        self.config = config
        self.redis: Optional[redis.Redis] = None

    async def connect(self):
        """Connect to Redis"""
        self.redis = await redis.Redis(
            host=self.config.host,
            port=self.config.port,
            decode_responses=False,
            max_connections=150,
        )

    async def close(self):
        """Close connection"""
        if self.redis:
            await self.redis.close()

    def _get_keys(self, key: str, use_hash_tags: bool = False) -> tuple:
        """Generate metadata and kv keys"""
        if use_hash_tags:
            return f"{{{key}}}:metadata", f"{{{key}}}:kv_bytes"
        return f"{key}:metadata", f"{key}:kv_bytes"

    async def write_keys(self) -> List[float]:
        """Write test keys and return latencies"""
        latencies = []
        value = b"x" * self.config.key_size

        for i in range(self.config.num_keys):
            key = f"bench:test:{i}"
            metadata_key, kv_key = self._get_keys(
                key, self.config.use_hash_tags
            )

            start = time.perf_counter()
            if self.config.use_batching:
                # Use pipeline for batched writes
                pipe = self.redis.pipeline()
                pipe.set(kv_key, value)
                pipe.set(metadata_key, b"metadata")
                await pipe.execute()
            else:
                # Individual SETs (baseline)
                await self.redis.set(kv_key, value)
                await self.redis.set(metadata_key, b"metadata")
            latencies.append(time.perf_counter() - start)

        return latencies

    async def read_keys_batch(self, batch_keys: List[str]) -> float:
        """Read a batch of keys and return latency"""
        if self.config.use_batching:
            # Batched MGET
            all_keys = []
            for key in batch_keys:
                metadata_key, kv_key = self._get_keys(
                    key, self.config.use_hash_tags
                )
                all_keys.extend([metadata_key, kv_key])

            start = time.perf_counter()
            results = await self.redis.mget(all_keys)
            latency = time.perf_counter() - start
            return latency
        else:
            # Individual GETs (baseline)
            start = time.perf_counter()
            for key in batch_keys:
                metadata_key, kv_key = self._get_keys(
                    key, self.config.use_hash_tags
                )
                await self.redis.get(metadata_key)
                await self.redis.get(kv_key)
            latency = time.perf_counter() - start
            return latency

    async def run_benchmark(self) -> BenchmarkResult:
        """Run full benchmark"""
        print(f"Running {self.config.mode} benchmark...")
        print(f"  Batching: {self.config.use_batching}")
        print(f"  Hash tags: {self.config.use_hash_tags}")
        print(f"  Key size: {self.config.key_size}B")
        print(f"  Batch size: {self.config.batch_size}")

        await self.connect()

        # Write phase
        print("  Writing keys...")
        write_latencies = await self.write_keys()

        # Read phase
        print("  Reading keys...")
        read_latencies = []
        all_keys = [f"bench:test:{i}" for i in range(self.config.num_keys)]

        for _ in range(self.config.num_iterations):
            # Random batch
            batch_keys = random.sample(
                all_keys, min(self.config.batch_size, len(all_keys))
            )
            latency = await self.read_keys_batch(batch_keys)
            read_latencies.append(latency)

        await self.close()

        # Calculate stats
        write_p50 = statistics.median(write_latencies)
        write_p95 = statistics.quantiles(write_latencies, n=20)[18]
        write_p99 = statistics.quantiles(write_latencies, n=100)[98]

        read_p50 = statistics.median(read_latencies)
        read_p95 = statistics.quantiles(read_latencies, n=20)[18]
        read_p99 = statistics.quantiles(read_latencies, n=100)[98]

        # Throughput
        total_read_time = sum(read_latencies)
        total_keys_read = self.config.num_iterations * self.config.batch_size
        read_ops_per_sec = total_keys_read / total_read_time if total_read_time > 0 else 0
        read_bytes_per_sec = (
            read_ops_per_sec * self.config.key_size
        )

        # Estimate round-trips
        if self.config.use_batching:
            # MGET is 1 round-trip for all keys
            estimated_rts = 1.0
        else:
            # Each key requires 2 GETs = 2 round-trips
            estimated_rts = self.config.batch_size * 2.0

        return BenchmarkResult(
            config=self.config,
            write_latency_p50=write_p50,
            write_latency_p95=write_p95,
            write_latency_p99=write_p99,
            read_latency_p50=read_p50,
            read_latency_p95=read_p95,
            read_latency_p99=read_p99,
            read_ops_per_sec=read_ops_per_sec,
            read_bytes_per_sec=read_bytes_per_sec,
            estimated_round_trips_per_request=estimated_rts,
        )


class RedisClusterBenchmark(RedisStandaloneBenchmark):
    """Benchmark for Redis Cluster with slot awareness"""

    async def connect(self):
        """Connect to Redis Cluster"""
        startup_nodes = [
            ClusterNode(host, port)
            for host, port in self.config.cluster_nodes
        ]
        self.redis = await RedisCluster(
            startup_nodes=startup_nodes,
            decode_responses=False,
            max_connections=150,
        )

    async def read_keys_batch(self, batch_keys: List[str]) -> float:
        """Read batch with slot awareness"""
        if self.config.use_batching and self.config.use_hash_tags:
            # All keys with hash tags should be on same slot
            all_keys = []
            for key in batch_keys:
                metadata_key, kv_key = self._get_keys(key, use_hash_tags=True)
                all_keys.extend([metadata_key, kv_key])

            start = time.perf_counter()
            results = await self.redis.mget(all_keys)
            latency = time.perf_counter() - start

            # Estimate 1 round-trip if all keys on same slot
            return latency
        elif self.config.use_batching and not self.config.use_hash_tags:
            # Keys may be on different slots - need to group by slot
            from collections import defaultdict

            slot_groups = defaultdict(list)
            for key in batch_keys:
                metadata_key, kv_key = self._get_keys(key, use_hash_tags=False)
                slot_m = self.redis.keyslot(metadata_key)
                slot_k = self.redis.keyslot(kv_key)
                slot_groups[slot_m].append(metadata_key)
                slot_groups[slot_k].append(kv_key)

            start = time.perf_counter()
            tasks = []
            for slot, keys in slot_groups.items():
                tasks.append(self.redis.mget(keys))
            await asyncio.gather(*tasks)
            latency = time.perf_counter() - start

            return latency
        else:
            # Baseline: individual GETs
            return await super().read_keys_batch(batch_keys)

    def estimate_round_trips(self) -> float:
        """Estimate round-trips per request for cluster"""
        if self.config.use_batching and self.config.use_hash_tags:
            # Hash tags: all keys on same slot = 1 round-trip
            return 1.0
        elif self.config.use_batching and not self.config.use_hash_tags:
            # Without hash tags: metadata and kv may be on different slots
            # Worst case: 2 slots (metadata on one, kv on another)
            # Average case: ~1.5 slots
            return 2.0
        else:
            # Baseline: each key = 2 GETs
            return self.config.batch_size * 2.0


async def run_benchmark_suite():
    """Run comprehensive benchmark suite"""
    results = []

    # Test configurations
    modes = ["standalone", "cluster"]
    key_sizes = [512, 2048, 8192]
    batch_sizes = [32, 64, 128, 256]
    batching_modes = [False, True]  # False = baseline, True = new batched

    for mode in modes:
        for key_size in key_sizes:
            for batch_size in batch_sizes:
                for use_batching in batching_modes:
                    config = BenchmarkConfig(
                        mode=mode,
                        host="localhost",
                        port=6379 if mode == "standalone" else 7000,
                        cluster_nodes=(
                            [("localhost", 7000), ("localhost", 7001), ("localhost", 7002)]
                            if mode == "cluster"
                            else None
                        ),
                        key_size=key_size,
                        batch_size=batch_size,
                        use_batching=use_batching,
                        use_hash_tags=True if mode == "cluster" else False,
                    )

                    try:
                        if mode == "standalone":
                            bench = RedisStandaloneBenchmark(config)
                        else:
                            bench = RedisClusterBenchmark(config)
                            config.estimated_round_trips_per_request = (
                                bench.estimate_round_trips()
                            )

                        result = await bench.run_benchmark()
                        results.append(result)

                        print(
                            f"  ✓ p50={result.read_latency_p50*1000:.2f}ms "
                            f"p95={result.read_latency_p95*1000:.2f}ms "
                            f"p99={result.read_latency_p99*1000:.2f}ms"
                        )
                    except Exception as e:
                        print(f"  ✗ Error: {e}")

    return results


def save_results_csv(results: List[BenchmarkResult], filename: str):
    """Save results to CSV"""
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "mode",
            "use_batching",
            "use_hash_tags",
            "key_size_bytes",
            "batch_size",
            "write_p50_ms",
            "write_p95_ms",
            "write_p99_ms",
            "read_p50_ms",
            "read_p95_ms",
            "read_p99_ms",
            "read_ops_per_sec",
            "read_mbps",
            "estimated_round_trips",
        ])

        for r in results:
            writer.writerow([
                r.config.mode,
                r.config.use_batching,
                r.config.use_hash_tags,
                r.config.key_size,
                r.config.batch_size,
                r.write_latency_p50 * 1000,
                r.write_latency_p95 * 1000,
                r.write_latency_p99 * 1000,
                r.read_latency_p50 * 1000,
                r.read_latency_p95 * 1000,
                r.read_latency_p99 * 1000,
                r.read_ops_per_sec,
                r.read_bytes_per_sec / (1024 * 1024),
                r.estimated_round_trips_per_request,
            ])

    print(f"\nResults saved to {filename}")


async def main():
    """Main entry point"""
    print("=" * 60)
    print("Redis Connector Batching Benchmark")
    print("=" * 60)

    results = await run_benchmark_suite()
    save_results_csv(results, "bench/results_ab.csv")

    # Print summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    for mode in ["standalone", "cluster"]:
        mode_results = [r for r in results if r.config.mode == mode]
        if not mode_results:
            continue

        print(f"\n{mode.upper()} MODE:")
        baseline = [r for r in mode_results if not r.config.use_batching]
        batched = [r for r in mode_results if r.config.use_batching]

        if baseline and batched:
            avg_baseline_p95 = statistics.mean(
                [r.read_latency_p95 for r in baseline]
            )
            avg_batched_p95 = statistics.mean(
                [r.read_latency_p95 for r in batched]
            )
            improvement = (
                (avg_baseline_p95 - avg_batched_p95) / avg_baseline_p95 * 100
            )
            print(f"  Average p95 improvement: {improvement:.1f}%")
            print(f"  Baseline avg p95: {avg_baseline_p95*1000:.2f}ms")
            print(f"  Batched avg p95: {avg_batched_p95*1000:.2f}ms")


if __name__ == "__main__":
    asyncio.run(main())
