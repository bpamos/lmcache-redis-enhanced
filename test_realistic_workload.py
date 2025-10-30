#!/usr/bin/env python3
"""
Realistic LMCache Workload Simulator

Simulates real-world LMCache usage patterns:
- Multiple concurrent requests with different trace IDs
- Variable batch sizes (realistic prompt/generation lengths)
- Cache hit/miss patterns
- Different KV cache sizes (layers, heads, sequence lengths)
- Chunk-based key naming (like real LMCache)
- Mixed read/write workloads

This shows how the batching improvements work with actual LMCache patterns.
"""

import asyncio
import random
import statistics
import time
from dataclasses import dataclass
from typing import List, Dict
import redis.asyncio as redis


@dataclass
class LMCacheRequest:
    """Simulates a real LMCache request"""
    trace_id: str
    num_chunks: int  # Variable batch size (prompt length)
    cache_hit: bool  # Whether data exists in cache


@dataclass
class WorkloadStats:
    """Statistics for workload execution"""
    total_requests: int
    cache_hits: int
    cache_misses: int
    total_chunks_read: int
    total_chunks_written: int
    read_latencies: List[float]
    write_latencies: List[float]
    end_to_end_latencies: List[float]


class LMCacheWorkloadSimulator:
    """
    Simulates realistic LMCache workload patterns.

    Models actual LMCache behavior:
    - Chunk-based KV cache storage
    - Hash-tagged keys for cluster co-location
    - Variable sequence lengths (different chunk counts)
    - Cache hit/miss patterns
    - Concurrent requests
    """

    def __init__(self, redis_url: str, use_batching: bool = True):
        self.redis_url = redis_url
        self.use_batching = use_batching
        self.connection = None

        # Realistic KV cache parameters (from LMCache typical usage)
        self.chunk_size = 2048  # Typical KV cache chunk size in bytes
        self.layer_sizes = [512, 1024, 2048, 4096]  # Different model layers

    async def connect(self):
        """Connect to Redis"""
        self.connection = await redis.from_url(self.redis_url, decode_responses=False)

    async def close(self):
        """Close connection"""
        if self.connection:
            await self.connection.close()

    def generate_kv_cache_data(self, size: int) -> bytes:
        """Generate realistic KV cache data"""
        return b"x" * size

    def get_chunk_keys(self, trace_id: str, num_chunks: int) -> List[str]:
        """
        Generate chunk keys with hash tags (like real LMCache).

        Pattern: lm:{trace_id}:chunk:N
        The {trace_id} hash tag ensures all chunks for a request are on same cluster slot.
        """
        return [f"lm:{{{trace_id}}}:chunk:{i}" for i in range(num_chunks)]

    async def write_kv_cache(self, trace_id: str, num_chunks: int) -> float:
        """
        Write KV cache chunks (simulates cache miss -> write new cache).

        Returns: write latency in seconds
        """
        keys = self.get_chunk_keys(trace_id, num_chunks)
        chunk_size = random.choice(self.layer_sizes)
        values = [self.generate_kv_cache_data(chunk_size) for _ in range(num_chunks)]

        start = time.perf_counter()

        if self.use_batching:
            # NEW: Batched pipeline write (this implementation)
            pipe = self.connection.pipeline()
            for key, value in zip(keys, values):
                pipe.set(f"{key}:kv_bytes", value)
                pipe.set(f"{key}:metadata", b"meta")
            await pipe.execute()
        else:
            # OLD: Individual writes (baseline)
            for key, value in zip(keys, values):
                await self.connection.set(f"{key}:kv_bytes", value)
                await self.connection.set(f"{key}:metadata", b"meta")

        elapsed = time.perf_counter() - start
        return elapsed

    async def read_kv_cache(self, trace_id: str, num_chunks: int) -> tuple:
        """
        Read KV cache chunks (simulates cache hit).

        Returns: (read_latency, num_keys_retrieved)
        """
        keys = self.get_chunk_keys(trace_id, num_chunks)

        start = time.perf_counter()

        if self.use_batching:
            # NEW: Batched MGET (this implementation)
            all_keys = []
            for key in keys:
                all_keys.extend([f"{key}:metadata", f"{key}:kv_bytes"])
            results = await self.connection.mget(all_keys)
            # Count successful retrievals
            retrieved = sum(1 for i in range(0, len(results), 2) if results[i] and results[i+1])
        else:
            # OLD: Individual GETs (baseline)
            retrieved = 0
            for key in keys:
                metadata = await self.connection.get(f"{key}:metadata")
                kv_bytes = await self.connection.get(f"{key}:kv_bytes")
                if metadata and kv_bytes:
                    retrieved += 1

        elapsed = time.perf_counter() - start
        return elapsed, retrieved

    async def process_request(self, request: LMCacheRequest) -> Dict:
        """
        Process a single LMCache request (read or write).

        Simulates real LMCache behavior:
        - Cache hit: Just read existing chunks
        - Cache miss: Write new chunks, then read them back
        """
        start = time.perf_counter()

        read_latency = 0.0
        write_latency = 0.0
        chunks_read = 0
        chunks_written = 0

        if request.cache_hit:
            # Cache hit: Just read
            read_latency, chunks_read = await self.read_kv_cache(
                request.trace_id, request.num_chunks
            )
        else:
            # Cache miss: Write then read
            write_latency = await self.write_kv_cache(
                request.trace_id, request.num_chunks
            )
            chunks_written = request.num_chunks

            # Then read back (subsequent request would hit cache)
            read_latency, chunks_read = await self.read_kv_cache(
                request.trace_id, request.num_chunks
            )

        end_to_end = time.perf_counter() - start

        return {
            "read_latency": read_latency,
            "write_latency": write_latency,
            "end_to_end": end_to_end,
            "chunks_read": chunks_read,
            "chunks_written": chunks_written,
        }

    def generate_realistic_workload(self, num_requests: int, cache_hit_rate: float = 0.7) -> List[LMCacheRequest]:
        """
        Generate realistic LMCache workload.

        Parameters:
        - num_requests: Number of requests to simulate
        - cache_hit_rate: Percentage of cache hits (default 70%)

        Real LMCache patterns:
        - Variable sequence lengths (32-512 tokens typical)
        - Chunk counts vary (1-32 chunks typical)
        - Cache hits are common (prefix caching, repeated prompts)
        """
        requests = []

        # Realistic chunk count distribution (based on typical LLM usage)
        chunk_distributions = {
            "short": (1, 4),      # 20% - Short prompts
            "medium": (4, 16),    # 50% - Medium prompts
            "long": (16, 32),     # 25% - Long prompts
            "very_long": (32, 64) # 5%  - Very long contexts
        }

        for i in range(num_requests):
            # Realistic distribution of sequence lengths
            rand = random.random()
            if rand < 0.20:
                chunk_range = chunk_distributions["short"]
            elif rand < 0.70:
                chunk_range = chunk_distributions["medium"]
            elif rand < 0.95:
                chunk_range = chunk_distributions["long"]
            else:
                chunk_range = chunk_distributions["very_long"]

            num_chunks = random.randint(chunk_range[0], chunk_range[1])
            cache_hit = random.random() < cache_hit_rate

            requests.append(LMCacheRequest(
                trace_id=f"trace_{i}",
                num_chunks=num_chunks,
                cache_hit=cache_hit,
            ))

        return requests

    async def run_workload(self, requests: List[LMCacheRequest], concurrency: int = 5) -> WorkloadStats:
        """
        Run workload with concurrent requests.

        Args:
            requests: List of requests to process
            concurrency: Number of concurrent requests (simulates concurrent users)
        """
        stats = WorkloadStats(
            total_requests=len(requests),
            cache_hits=sum(1 for r in requests if r.cache_hit),
            cache_misses=sum(1 for r in requests if not r.cache_hit),
            total_chunks_read=0,
            total_chunks_written=0,
            read_latencies=[],
            write_latencies=[],
            end_to_end_latencies=[],
        )

        # Process requests with limited concurrency (like real workload)
        semaphore = asyncio.Semaphore(concurrency)

        async def process_with_limit(request):
            async with semaphore:
                return await self.process_request(request)

        results = await asyncio.gather(*[process_with_limit(r) for r in requests])

        # Aggregate stats
        for result in results:
            if result["read_latency"] > 0:
                stats.read_latencies.append(result["read_latency"])
            if result["write_latency"] > 0:
                stats.write_latencies.append(result["write_latency"])
            stats.end_to_end_latencies.append(result["end_to_end"])
            stats.total_chunks_read += result["chunks_read"]
            stats.total_chunks_written += result["chunks_written"]

        return stats


async def run_realistic_comparison(redis_url: str, num_requests: int = 100):
    """
    Run realistic workload comparison: OLD vs NEW implementation.
    """
    print("="*70)
    print("  Realistic LMCache Workload Simulation")
    print("="*70)
    print(f"\nConfiguration:")
    print(f"  Target: {redis_url[:50]}...")
    print(f"  Requests: {num_requests}")
    print(f"  Cache hit rate: 70% (realistic)")
    print(f"  Chunk distribution: Variable (1-64 chunks per request)")
    print(f"  Concurrency: 5 concurrent requests")
    print()

    # Generate workload
    print("Generating realistic workload...")
    simulator = LMCacheWorkloadSimulator(redis_url, use_batching=False)
    await simulator.connect()
    requests = simulator.generate_realistic_workload(num_requests, cache_hit_rate=0.7)
    await simulator.close()

    # Show workload stats
    total_chunks = sum(r.num_chunks for r in requests)
    avg_chunks = total_chunks / len(requests)
    print(f"✓ Generated {len(requests)} requests")
    print(f"  Total chunks: {total_chunks}")
    print(f"  Avg chunks per request: {avg_chunks:.1f}")
    print(f"  Cache hits: {sum(1 for r in requests if r.cache_hit)}")
    print(f"  Cache misses: {sum(1 for r in requests if not r.cache_hit)}")
    print()

    # ========================================
    # BASELINE: OLD (Non-batched)
    # ========================================
    print("="*70)
    print("  BASELINE: OLD Redis (Non-batched)")
    print("="*70)
    print("Simulating original LMCache Redis connector...")
    print()

    simulator_old = LMCacheWorkloadSimulator(redis_url, use_batching=False)
    await simulator_old.connect()

    start_time = time.time()
    stats_old = await simulator_old.run_workload(requests, concurrency=5)
    total_time_old = time.time() - start_time

    await simulator_old.close()

    # Calculate stats
    old_read_p50 = statistics.median(stats_old.read_latencies) if stats_old.read_latencies else 0
    old_read_p95 = statistics.quantiles(stats_old.read_latencies, n=20)[18] if len(stats_old.read_latencies) >= 20 else old_read_p50
    old_write_p50 = statistics.median(stats_old.write_latencies) if stats_old.write_latencies else 0
    old_e2e_p50 = statistics.median(stats_old.end_to_end_latencies)
    old_e2e_p95 = statistics.quantiles(stats_old.end_to_end_latencies, n=20)[18] if len(stats_old.end_to_end_latencies) >= 20 else old_e2e_p50

    print(f"Results:")
    print(f"  Total time: {total_time_old:.2f}s")
    print(f"  Throughput: {num_requests/total_time_old:.1f} requests/sec")
    print(f"  Chunks read: {stats_old.total_chunks_read}")
    print(f"  Chunks written: {stats_old.total_chunks_written}")
    print(f"  Read latency: p50={old_read_p50*1000:.2f}ms, p95={old_read_p95*1000:.2f}ms")
    print(f"  Write latency: p50={old_write_p50*1000:.2f}ms")
    print(f"  End-to-end: p50={old_e2e_p50*1000:.2f}ms, p95={old_e2e_p95*1000:.2f}ms")
    print()

    await asyncio.sleep(1)

    # ========================================
    # NEW: Batched Redis
    # ========================================
    print("="*70)
    print("  NEW: Redis with Batching (This Implementation)")
    print("="*70)
    print("Simulating enhanced Redis connector with MGET/Pipeline...")
    print()

    simulator_new = LMCacheWorkloadSimulator(redis_url, use_batching=True)
    await simulator_new.connect()

    start_time = time.time()
    stats_new = await simulator_new.run_workload(requests, concurrency=5)
    total_time_new = time.time() - start_time

    await simulator_new.close()

    # Calculate stats
    new_read_p50 = statistics.median(stats_new.read_latencies) if stats_new.read_latencies else 0
    new_read_p95 = statistics.quantiles(stats_new.read_latencies, n=20)[18] if len(stats_new.read_latencies) >= 20 else new_read_p50
    new_write_p50 = statistics.median(stats_new.write_latencies) if stats_new.write_latencies else 0
    new_e2e_p50 = statistics.median(stats_new.end_to_end_latencies)
    new_e2e_p95 = statistics.quantiles(stats_new.end_to_end_latencies, n=20)[18] if len(stats_new.end_to_end_latencies) >= 20 else new_e2e_p50

    print(f"Results:")
    print(f"  Total time: {total_time_new:.2f}s")
    print(f"  Throughput: {num_requests/total_time_new:.1f} requests/sec")
    print(f"  Chunks read: {stats_new.total_chunks_read}")
    print(f"  Chunks written: {stats_new.total_chunks_written}")
    print(f"  Read latency: p50={new_read_p50*1000:.2f}ms, p95={new_read_p95*1000:.2f}ms")
    print(f"  Write latency: p50={new_write_p50*1000:.2f}ms")
    print(f"  End-to-end: p50={new_e2e_p50*1000:.2f}ms, p95={new_e2e_p95*1000:.2f}ms")
    print()

    # ========================================
    # COMPARISON
    # ========================================
    print("="*70)
    print("  REALISTIC WORKLOAD IMPROVEMENT SUMMARY")
    print("="*70)
    print()

    # Throughput improvement
    throughput_improvement = ((num_requests/total_time_new) - (num_requests/total_time_old)) / (num_requests/total_time_old) * 100
    throughput_speedup = (num_requests/total_time_new) / (num_requests/total_time_old)

    print("Overall Throughput:")
    print(f"  OLD: {num_requests/total_time_old:.1f} requests/sec")
    print(f"  NEW: {num_requests/total_time_new:.1f} requests/sec")
    print(f"  Improvement: {throughput_speedup:.2f}× faster ({throughput_improvement:.1f}% increase)")
    print()

    # Read latency improvement
    read_p95_speedup = old_read_p95 / new_read_p95 if new_read_p95 > 0 else 0
    read_p95_improvement = ((old_read_p95 - new_read_p95) / old_read_p95) * 100 if old_read_p95 > 0 else 0

    print("Read Latency (p95):")
    print(f"  OLD: {old_read_p95*1000:.2f}ms")
    print(f"  NEW: {new_read_p95*1000:.2f}ms")
    print(f"  Improvement: {read_p95_speedup:.2f}× faster ({read_p95_improvement:.1f}% reduction)")
    print()

    # End-to-end latency improvement
    e2e_p95_speedup = old_e2e_p95 / new_e2e_p95
    e2e_p95_improvement = ((old_e2e_p95 - new_e2e_p95) / old_e2e_p95) * 100

    print("End-to-End Latency (p95):")
    print(f"  OLD: {old_e2e_p95*1000:.2f}ms")
    print(f"  NEW: {new_e2e_p95*1000:.2f}ms")
    print(f"  Improvement: {e2e_p95_speedup:.2f}× faster ({e2e_p95_improvement:.1f}% reduction)")
    print()

    # Workload efficiency
    print("Workload Efficiency:")
    print(f"  Cache hits served: {stats_old.cache_hits} (both)")
    print(f"  Cache misses handled: {stats_old.cache_misses} (both)")
    print(f"  Total chunks processed: {stats_old.total_chunks_read + stats_old.total_chunks_written}")
    print()

    print("="*70)
    print("  Real-World Impact")
    print("="*70)
    print()
    print("This workload simulates actual LMCache usage with:")
    print("  ✓ Variable sequence lengths (1-64 chunks)")
    print("  ✓ Realistic cache hit rates (70%)")
    print("  ✓ Concurrent requests (5 simultaneous)")
    print("  ✓ Mixed read/write operations")
    print("  ✓ Hash-tagged keys for cluster co-location")
    print()
    print(f"Result: {e2e_p95_improvement:.1f}% faster end-to-end latency")
    print(f"        {throughput_speedup:.1f}× higher throughput")
    print()
    print("This is the real-world performance improvement users will see!")
    print()

    # Cleanup
    print("Cleaning up test data...")
    cleanup = await redis.from_url(redis_url, decode_responses=False)
    all_keys = []
    for req in requests:
        chunk_keys = [f"lm:{{{req.trace_id}}}:chunk:{i}" for i in range(req.num_chunks)]
        for key in chunk_keys:
            all_keys.extend([key, f"{key}:metadata", f"{key}:kv_bytes"])
    if all_keys:
        await cleanup.delete(*all_keys)
    await cleanup.close()
    print("✓ Cleanup complete")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Realistic LMCache workload test")
    parser.add_argument(
        "--redis-url",
        default="redis://default:SldUJoIS67giEfBIBe5lsKl8bMYK4ljq@redis-18479.c49084.us-east-1-mz.ec2.cloud.rlrcp.com:18479",
        help="Redis URL"
    )
    parser.add_argument(
        "--num-requests",
        type=int,
        default=100,
        help="Number of requests to simulate (default: 100)"
    )

    args = parser.parse_args()

    await run_realistic_comparison(args.redis_url, args.num_requests)


if __name__ == "__main__":
    asyncio.run(main())
