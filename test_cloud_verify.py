#!/usr/bin/env python3
"""
Quick verification test that leaves data in Redis so you can see it in monitoring.
"""
import asyncio
import redis.asyncio as redis
import time

async def main():
    import os
    import sys

    url = os.environ.get("REDIS_URL", "redis://localhost:6379")

    if url == "redis://localhost:6379":
        print("WARNING: No REDIS_URL environment variable set!")
        print("Using default: redis://localhost:6379")
        print()
        print("To use Redis Cloud, set: export REDIS_URL='rediss://...'")
        print()

    print(f"Connecting to Redis at {url[:30]}...")
    client = await redis.from_url(url, decode_responses=False)

    print("✓ Connected!")
    print()

    # PING
    print("Testing PING...")
    pong = await client.ping()
    print(f"✓ PONG: {pong}")
    print()

    # Write some test keys (DON'T delete them)
    print("Writing 20 test keys (these will STAY in your database)...")
    for i in range(20):
        await client.set(f"test:verify:{i}", f"value_{i}".encode())
    print("✓ Wrote 20 keys with prefix 'test:verify:*'")
    print()

    # Test MGET batching
    print("Testing batched MGET...")
    keys = [f"test:verify:{i}" for i in range(20)]
    start = time.time()
    results = await client.mget(keys)
    elapsed = (time.time() - start) * 1000
    print(f"✓ MGET retrieved {len(results)} keys in {elapsed:.2f}ms")
    print()

    # Check how many keys exist
    print("Checking total keys in database...")
    info = await client.info("keyspace")
    print(f"Database info: {info}")
    print()

    # Show some keys
    print("Sample of keys in database:")
    all_keys = await client.keys("test:verify:*")
    print(f"✓ Found {len(all_keys)} keys with prefix 'test:verify:*'")
    print(f"  First 5: {all_keys[:5]}")
    print()

    print("="*60)
    print("NOW CHECK YOUR REDIS CLOUD MONITORING DASHBOARD!")
    print("="*60)
    print()
    print("You should see:")
    print("  - 20+ keys in the database")
    print("  - Recent operations (SET, GET, MGET, etc.)")
    print()
    print("To clean up these test keys, run:")
    print("  redis-cli -u <your-url> DEL test:verify:*")
    print()

    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
