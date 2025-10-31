#!/usr/bin/env python3
"""
Load test for Redis - runs continuous operations for a specified duration.
"""
import asyncio
import argparse
import redis.asyncio as redis
import time
from datetime import datetime

class LoadTester:
    def __init__(self, url: str, duration_seconds: int = 60):
        self.url = url
        self.duration = duration_seconds
        self.operations = 0
        self.errors = 0
        self.start_time = None
        self.running = True

    async def run_worker(self, worker_id: int):
        """Single worker that performs continuous operations"""
        client = await redis.from_url(self.url, decode_responses=False)

        try:
            while self.running:
                # Write 10 keys
                for i in range(10):
                    key = f"load:worker{worker_id}:key{i}"
                    value = f"value_{int(time.time())}_{i}".encode()
                    await client.set(key, value)
                    self.operations += 1

                # Batch read with MGET
                keys = [f"load:worker{worker_id}:key{i}" for i in range(10)]
                await client.mget(keys)
                self.operations += 1

                # Individual reads
                for i in range(5):
                    key = f"load:worker{worker_id}:key{i}"
                    await client.get(key)
                    self.operations += 1

                # Pipeline write
                pipe = client.pipeline()
                for i in range(10):
                    key = f"load:worker{worker_id}:pipe{i}"
                    pipe.set(key, f"pipe_value_{i}".encode())
                await pipe.execute()
                self.operations += 1

        except Exception as e:
            self.errors += 1
            print(f"Worker {worker_id} error: {e}")
        finally:
            await client.close()

    async def stats_reporter(self):
        """Report stats every second"""
        last_ops = 0

        while self.running:
            await asyncio.sleep(1)
            elapsed = time.time() - self.start_time
            current_ops = self.operations
            ops_per_sec = current_ops - last_ops
            last_ops = current_ops

            print(f"[{elapsed:5.1f}s] Operations: {current_ops:,} | Ops/sec: {ops_per_sec:,} | Errors: {self.errors}")

    async def run(self, num_workers: int = 5):
        """Run the load test"""
        print("="*60)
        print(f"  Redis Load Test")
        print("="*60)
        print(f"Target: {self.url[:50]}...")
        print(f"Duration: {self.duration} seconds")
        print(f"Workers: {num_workers}")
        print()
        print("Starting load test... CHECK YOUR DASHBOARD NOW!")
        print()

        self.start_time = time.time()

        # Start workers
        workers = [asyncio.create_task(self.run_worker(i)) for i in range(num_workers)]

        # Start stats reporter
        reporter = asyncio.create_task(self.stats_reporter())

        # Run for specified duration
        await asyncio.sleep(self.duration)

        # Stop workers
        self.running = False
        await asyncio.gather(*workers, return_exceptions=True)
        reporter.cancel()

        # Final stats
        elapsed = time.time() - self.start_time
        avg_ops_per_sec = self.operations / elapsed

        print()
        print("="*60)
        print("  Load Test Complete")
        print("="*60)
        print(f"Total operations: {self.operations:,}")
        print(f"Duration: {elapsed:.1f} seconds")
        print(f"Average ops/sec: {avg_ops_per_sec:.0f}")
        print(f"Errors: {self.errors}")
        print()

    async def cleanup(self):
        """Clean up all test keys"""
        print("Cleaning up test keys...")
        client = await redis.from_url(self.url, decode_responses=False)

        # Find all load test keys
        keys = await client.keys("load:*")
        if keys:
            print(f"Deleting {len(keys)} keys...")
            await client.delete(*keys)
            print("✓ Cleanup complete")
        else:
            print("✓ No keys to clean up")

        await client.close()


async def main():
    import os
    parser = argparse.ArgumentParser(description="Redis load tester")
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        help="Redis URL (default: redis://localhost:6379, or $REDIS_URL env var)"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Duration in seconds (default: 60)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Number of concurrent workers (default: 5)"
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Don't clean up test keys after test"
    )

    args = parser.parse_args()

    tester = LoadTester(args.redis_url, args.duration)

    try:
        await tester.run(args.workers)
    finally:
        if not args.no_cleanup:
            await tester.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
