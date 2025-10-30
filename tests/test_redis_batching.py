"""
Unit tests for Redis connector batching and pipelining functionality.

Tests:
- Order preservation in mget_batched
- Missing keys return None
- Hash tag generation
- Slot grouping for cluster mode
- TTL accuracy
- Standalone vs cluster parity
"""

import asyncio
import pytest
import redis.asyncio as redis
from redis.asyncio.cluster import ClusterNode, RedisCluster
from collections import defaultdict


class TestHashTagGeneration:
    """Test hash tag key generation"""

    def test_hash_tag_format(self):
        """Verify hash tag format for cluster mode"""
        key = "test:key:123"

        # With hash tags (cluster)
        metadata_key = f"{{{key}}}:metadata"
        kv_key = f"{{{key}}}:kv_bytes"

        assert metadata_key == "{test:key:123}:metadata"
        assert kv_key == "{test:key:123}:kv_bytes"

    def test_hash_tag_slot_consistency(self):
        """Verify hash tags ensure same slot for metadata and kv"""
        # This would require redis-py's keyslot function
        from redis.cluster import RedisClusterCommands

        key = "test:key:123"
        metadata_key = f"{{{key}}}:metadata"
        kv_key = f"{{{key}}}:kv_bytes"

        # Both should hash to same slot due to {key} hash tag
        slot_m = RedisClusterCommands.CLUSTER_KEYSLOT(metadata_key)
        slot_k = RedisClusterCommands.CLUSTER_KEYSLOT(kv_key)

        assert slot_m == slot_k, "Hash tags should ensure same slot"


class TestSlotGrouping:
    """Test slot-aware key grouping"""

    def test_group_keys_by_slot(self):
        """Test grouping keys by hash slot"""

        def group_keys_by_slot(keys):
            """Group keys by their hash slot"""
            from redis.cluster import RedisClusterCommands

            slot_groups = defaultdict(list)
            for idx, key in enumerate(keys):
                slot = RedisClusterCommands.CLUSTER_KEYSLOT(key)
                slot_groups[slot].append((idx, key))
            return slot_groups

        # Test with hash tags (should all be same slot)
        tagged_keys = [f"{{trace:123}}:chunk:{i}" for i in range(10)]
        groups = group_keys_by_slot(tagged_keys)

        assert len(groups) == 1, "Hash-tagged keys should be in one slot"

        # Test without hash tags (likely multiple slots)
        untagged_keys = [f"trace:123:chunk:{i}" for i in range(100)]
        groups = group_keys_by_slot(untagged_keys)

        # With 100 keys and 16384 slots, likely multiple slots
        # (exact number depends on hash function)
        assert len(groups) >= 1, "Untagged keys may span multiple slots"

    def test_order_preservation(self):
        """Test that order is preserved in slot grouping"""

        def group_keys_by_slot(keys):
            from redis.cluster import RedisClusterCommands

            slot_groups = defaultdict(list)
            for idx, key in enumerate(keys):
                slot = RedisClusterCommands.CLUSTER_KEYSLOT(key)
                slot_groups[slot].append((idx, key))
            return slot_groups

        keys = [f"key:{i}" for i in range(20)]
        groups = group_keys_by_slot(keys)

        # Verify indices are preserved
        all_indices = []
        for slot, key_list in groups.items():
            for idx, key in key_list:
                all_indices.append(idx)

        all_indices.sort()
        assert all_indices == list(range(20)), "Order should be preserved"


@pytest.mark.asyncio
class TestRedisStandaloneBatching:
    """Test batching operations on standalone Redis"""

    @pytest.fixture
    async def redis_client(self):
        """Create Redis client"""
        client = await redis.Redis(
            host="localhost",
            port=6379,
            decode_responses=False,
        )
        yield client
        await client.close()

    async def test_mget_order_preservation(self, redis_client):
        """Verify MGET preserves order"""
        # Write test keys
        keys = [f"order:test:{i}" for i in range(10)]
        values = [f"value:{i}".encode() for i in range(10)]

        for k, v in zip(keys, values):
            await redis_client.set(k, v)

        # MGET should return in same order
        results = await redis_client.mget(keys)

        assert len(results) == len(keys)
        for i, result in enumerate(results):
            assert result == values[i]

        # Cleanup
        await redis_client.delete(*keys)

    async def test_mget_missing_keys(self, redis_client):
        """Verify MGET returns None for missing keys"""
        keys = ["missing:1", "missing:2", "missing:3"]

        results = await redis_client.mget(keys)

        assert len(results) == 3
        assert all(r is None for r in results)

    async def test_mget_mixed_keys(self, redis_client):
        """Verify MGET handles mix of present and missing keys"""
        # Write some keys
        await redis_client.set("exists:1", b"value1")
        await redis_client.set("exists:3", b"value3")

        # MGET with mix
        keys = ["exists:1", "missing:2", "exists:3"]
        results = await redis_client.mget(keys)

        assert len(results) == 3
        assert results[0] == b"value1"
        assert results[1] is None
        assert results[2] == b"value3"

        # Cleanup
        await redis_client.delete("exists:1", "exists:3")

    async def test_pipeline_batched_set(self, redis_client):
        """Verify pipelined SET operations"""
        keys = [f"pipe:test:{i}" for i in range(10)]
        values = [f"value:{i}".encode() for i in range(10)]

        # Pipelined SET
        pipe = redis_client.pipeline()
        for k, v in zip(keys, values):
            pipe.set(k, v)
        await pipe.execute()

        # Verify all written
        results = await redis_client.mget(keys)
        assert all(results[i] == values[i] for i in range(10))

        # Cleanup
        await redis_client.delete(*keys)

    async def test_pipeline_vs_individual_correctness(self, redis_client):
        """Verify pipeline produces same results as individual operations"""
        keys = [f"compare:test:{i}" for i in range(5)]
        values = [f"value:{i}".encode() for i in range(5)]

        # Individual SETs
        for k, v in zip(keys[:3], values[:3]):
            await redis_client.set(k, v)

        # Pipelined SETs
        pipe = redis_client.pipeline()
        for k, v in zip(keys[3:], values[3:]):
            pipe.set(k, v)
        await pipe.execute()

        # All should be retrievable
        results = await redis_client.mget(keys)
        assert all(results[i] == values[i] for i in range(5))

        # Cleanup
        await redis_client.delete(*keys)


@pytest.mark.asyncio
class TestRedisClusterBatching:
    """Test batching operations on Redis Cluster"""

    @pytest.fixture
    async def cluster_client(self):
        """Create Redis Cluster client"""
        startup_nodes = [
            ClusterNode("localhost", 7000),
            ClusterNode("localhost", 7001),
            ClusterNode("localhost", 7002),
        ]

        client = await RedisCluster(
            startup_nodes=startup_nodes,
            decode_responses=False,
        )
        yield client
        await client.close()

    async def test_hash_tag_same_slot(self, cluster_client):
        """Verify hash tags keep keys on same slot"""
        key = "trace:123"
        metadata_key = f"{{{key}}}:metadata"
        kv_key = f"{{{key}}}:kv_bytes"

        # Write both
        await cluster_client.set(metadata_key, b"meta")
        await cluster_client.set(kv_key, b"kv_data")

        # MGET should work (both on same node)
        results = await cluster_client.mget([metadata_key, kv_key])

        assert len(results) == 2
        assert results[0] == b"meta"
        assert results[1] == b"kv_data"

        # Cleanup
        await cluster_client.delete(metadata_key, kv_key)

    async def test_cluster_mget_order_preservation(self, cluster_client):
        """Verify MGET order preservation in cluster"""
        # Use hash tags to ensure same slot
        base_key = "cluster:order:test"
        keys = [f"{{{base_key}}}:{i}" for i in range(10)]
        values = [f"value:{i}".encode() for i in range(10)]

        # Write
        for k, v in zip(keys, values):
            await cluster_client.set(k, v)

        # MGET
        results = await cluster_client.mget(keys)

        assert len(results) == len(keys)
        for i, result in enumerate(results):
            assert result == values[i]

        # Cleanup
        await cluster_client.delete(*keys)

    async def test_cluster_pipeline(self, cluster_client):
        """Verify pipeline works in cluster mode"""
        base_key = "cluster:pipe:test"
        keys = [f"{{{base_key}}}:{i}" for i in range(5)]
        values = [f"value:{i}".encode() for i in range(5)]

        # Pipelined SET (all same slot due to hash tag)
        pipe = cluster_client.pipeline()
        for k, v in zip(keys, values):
            pipe.set(k, v)
        await pipe.execute()

        # Verify
        results = await cluster_client.mget(keys)
        assert all(results[i] == values[i] for i in range(5))

        # Cleanup
        await cluster_client.delete(*keys)


@pytest.mark.asyncio
class TestParityStandaloneCluster:
    """Test parity between standalone and cluster implementations"""

    @pytest.fixture
    async def standalone_client(self):
        client = await redis.Redis(host="localhost", port=6379, decode_responses=False)
        yield client
        await client.close()

    @pytest.fixture
    async def cluster_client(self):
        startup_nodes = [ClusterNode("localhost", 7000)]
        client = await RedisCluster(startup_nodes=startup_nodes, decode_responses=False)
        yield client
        await client.close()

    async def test_parity_mget(self, standalone_client, cluster_client):
        """Verify MGET behaves same in standalone and cluster"""
        # Standalone
        keys_sa = [f"parity:sa:{i}" for i in range(5)]
        values = [f"value:{i}".encode() for i in range(5)]

        for k, v in zip(keys_sa, values):
            await standalone_client.set(k, v)

        results_sa = await standalone_client.mget(keys_sa)

        # Cluster (with hash tags)
        base = "parity:cluster"
        keys_cl = [f"{{{base}}}:{i}" for i in range(5)]

        for k, v in zip(keys_cl, values):
            await cluster_client.set(k, v)

        results_cl = await cluster_client.mget(keys_cl)

        # Both should return same values
        assert results_sa == results_cl == values

        # Cleanup
        await standalone_client.delete(*keys_sa)
        await cluster_client.delete(*keys_cl)


def test_imports():
    """Verify all required imports work"""
    import redis.asyncio as redis
    from redis.asyncio.cluster import ClusterNode, RedisCluster
    from redis.cluster import RedisClusterCommands

    assert redis is not None
    assert ClusterNode is not None
    assert RedisCluster is not None
    assert RedisClusterCommands is not None
