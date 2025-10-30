# SPDX-License-Identifier: Apache-2.0
# Standard
from collections import defaultdict
from enum import IntEnum, auto
from typing import Dict, List, Optional, Tuple, no_type_check
import asyncio
import inspect
import os

# Third Party
from redis.asyncio.cluster import ClusterNode, RedisCluster
from redis.cluster import RedisClusterCommands
import redis.asyncio as redis

# First Party
from lmcache.logging import init_logger
from lmcache.utils import CacheEngineKey
from lmcache.v1.memory_management import MemoryObj
from lmcache.v1.protocol import RemoteMetadata
from lmcache.v1.storage_backend.connector.base_connector import RemoteConnector
from lmcache.v1.storage_backend.job_executor.pq_executor import AsyncPQExecutor
from lmcache.v1.storage_backend.local_cpu_backend import LocalCPUBackend

logger = init_logger(__name__)


class Priorities(IntEnum):
    PEEK = auto()
    PREFETCH = auto()
    GET = auto()
    PUT = auto()


class RedisConnector(RemoteConnector):
    """
    Enhanced Redis connector with batching and pipelining support.

    Supports standalone Redis with:
    - MGET for batched reads
    - Pipelined SET operations for batched writes
    - Configurable chunk sizes
    - Connection pooling
    """

    def __init__(
        self,
        url: str,
        loop: asyncio.AbstractEventLoop,
        local_cpu_backend: LocalCPUBackend,
        username: str = "",
        password: str = "",
        database_id: Optional[int] = None,
        chunk_size: int = 256,
        max_connections: int = 150,
        use_tls: bool = False,
    ):
        self.url = url
        self.username = username
        self.password = password
        self.database_id = database_id
        self.chunk_size = chunk_size
        self.max_connections = max_connections
        self.use_tls = use_tls or url.startswith("rediss://")
        self.loop = loop
        self.local_cpu_backend = local_cpu_backend
        self.executor = AsyncPQExecutor(loop)

        # Connection limiting
        self.sem = asyncio.Semaphore(max_connections)

        # Create connection pool
        self.connection = self._init_connection()

    def _init_connection(self):
        """Initialize Redis connection with credentials and database"""

        async def create_connection():
            try:
                # Build connection URL with auth if provided
                conn_url = self.url
                if self.username or self.password:
                    # Parse the URL and inject credentials
                    if "://" in conn_url:
                        scheme, rest = conn_url.split("://", 1)
                        if self.username and self.password:
                            conn_url = f"{scheme}://{self.username}:{self.password}@{rest}"
                        elif self.username:
                            conn_url = f"{scheme}://{self.username}@{rest}"

                # Create connection pool
                pool_kwargs = {
                    "max_connections": self.max_connections,
                    "decode_responses": False,
                    "db": self.database_id if self.database_id is not None else 0,
                }
                if self.use_tls:
                    pool_kwargs["ssl"] = True
                    pool_kwargs["ssl_cert_reqs"] = None  # Don't verify certs for simplicity

                pool = redis.ConnectionPool.from_url(
                    conn_url,
                    **pool_kwargs
                )
                return redis.Redis.from_pool(pool)
            except Exception as e:
                raise RuntimeError(f"Failed to init redis connection: {e}") from e

        future = asyncio.run_coroutine_threadsafe(create_connection(), self.loop)
        connection = future.result(timeout=10.0)
        return connection

    def _get_keys(self, key: CacheEngineKey) -> Tuple[str, str]:
        """Generate metadata and kv_bytes keys"""
        key_str = key.to_string()
        metadata_key = f"{key_str}:metadata"
        kv_key = f"{key_str}:kv_bytes"
        return metadata_key, kv_key

    async def _exists(self, key: CacheEngineKey) -> bool:
        metadata_key, _ = self._get_keys(key)
        async with self.sem:
            return bool(await self.connection.exists(metadata_key))

    async def exists(self, key: CacheEngineKey) -> bool:
        return await self.executor.submit_job(
            self._exists, key=key, priority=Priorities.PEEK
        )

    def exists_sync(self, key: CacheEngineKey) -> bool:
        future = asyncio.run_coroutine_threadsafe(
            self.executor.submit_job(self._exists, key=key, priority=Priorities.PEEK),
            self.loop,
        )
        return future.result()

    async def _get(self, key: CacheEngineKey) -> Optional[MemoryObj]:
        """Get a single key using MGET for consistency with batched operations"""
        metadata_key, kv_key = self._get_keys(key)

        async with self.sem:
            # Use MGET to fetch both keys in one round-trip
            results = await self.connection.mget([metadata_key, kv_key])

        if len(results) != 2:
            return None

        metadata_bytes, kv_bytes = results[0], results[1]

        if metadata_bytes is None:
            return None

        assert not inspect.isawaitable(metadata_bytes)

        metadata = RemoteMetadata.deserialize(memoryview(metadata_bytes))

        memory_obj = self.local_cpu_backend.allocate(
            metadata.shape,
            metadata.dtype,
            metadata.fmt,
        )
        if memory_obj is None:
            logger.warning("Failed to allocate memory during remote receive")
            return None

        if kv_bytes is None:
            logger.warning(
                "Key exists but KV cache does not exist. "
                "Might happen when the cache is evicted by redis."
            )
            memory_obj.ref_count_down()
            return None

        assert not inspect.isawaitable(kv_bytes)

        try:
            if isinstance(memory_obj.byte_array, memoryview):
                view = memory_obj.byte_array
                if view.format == "<B":
                    view = view.cast("B")
            else:
                view = memoryview(memory_obj.byte_array)

            if isinstance(kv_bytes, (bytes, bytearray)):
                view[: metadata.length] = kv_bytes
            elif isinstance(kv_bytes, str):
                converted = kv_bytes.encode("utf-8")
                view[: metadata.length] = converted
            else:
                converted = bytes(kv_bytes)
                view[: metadata.length] = converted
            return memory_obj

        except Exception as exc:
            logger.error(f"Failed to convert data: {exc}")
            return None

    async def get(self, key: CacheEngineKey) -> Optional[MemoryObj]:
        return await self.executor.submit_job(
            self._get, key=key, priority=Priorities.GET
        )

    async def _put(self, key: CacheEngineKey, memory_obj: MemoryObj):
        """Put a single key-value pair using pipeline"""
        try:
            kv_bytes = bytes(memory_obj.byte_array)
            kv_shape = memory_obj.get_shape()
            kv_dtype = memory_obj.get_dtype()
            memory_format = memory_obj.get_memory_format()

            metadata_bytes = RemoteMetadata(
                len(kv_bytes), kv_shape, kv_dtype, memory_format
            ).serialize()

            metadata_key, kv_key = self._get_keys(key)

            # Use pipeline to set both keys in one round-trip
            # kv bytes needs to be set first to avoid race condition
            async with self.sem:
                pipe = self.connection.pipeline()
                pipe.set(kv_key, kv_bytes)
                pipe.set(metadata_key, metadata_bytes)
                await pipe.execute()

        except Exception as exc:
            logger.error(f"Failed to put data: {exc}")

    async def put(self, key: CacheEngineKey, memory_obj: MemoryObj):
        await self.executor.submit_job(
            self._put, key=key, memory_obj=memory_obj, priority=Priorities.PUT
        )

    def support_batched_put(self) -> bool:
        return True

    async def _batched_put(
        self, keys: List[CacheEngineKey], memory_objs: List[MemoryObj]
    ):
        """Batched put with pipelining"""
        try:
            async with self.sem:
                pipe = self.connection.pipeline()
                for key, memory_obj in zip(keys, memory_objs, strict=False):
                    kv_bytes = bytes(memory_obj.byte_array)
                    kv_shape = memory_obj.get_shape()
                    kv_dtype = memory_obj.get_dtype()
                    memory_format = memory_obj.get_memory_format()

                    metadata_bytes = RemoteMetadata(
                        len(kv_bytes), kv_shape, kv_dtype, memory_format
                    ).serialize()

                    metadata_key, kv_key = self._get_keys(key)

                    # kv bytes needs to be set first to avoid race condition
                    pipe.set(kv_key, kv_bytes)
                    pipe.set(metadata_key, metadata_bytes)

                await pipe.execute()
        except Exception as exc:
            logger.error(f"Failed to batched put: {exc}")

    async def batched_put(
        self, keys: List[CacheEngineKey], memory_objs: List[MemoryObj]
    ):
        await self.executor.submit_job(
            self._batched_put,
            keys=keys,
            memory_objs=memory_objs,
            priority=Priorities.PUT,
        )

    def support_batched_async_contains(self) -> bool:
        return True

    async def _batched_async_contains(
        self,
        lookup_id: str,
        keys: List[CacheEngineKey],
        pin: bool = False,
    ) -> int:
        """Batched exists check using MGET on metadata keys"""
        metadata_keys = [self._get_keys(key)[0] for key in keys]

        async with self.sem:
            # Use EXISTS with multiple keys for efficient checking
            results = await self.connection.exists(*metadata_keys)

        return results

    async def batched_async_contains(
        self,
        lookup_id: str,
        keys: List[CacheEngineKey],
        pin: bool = False,
    ) -> int:
        return await self.executor.submit_job(
            self._batched_async_contains,
            lookup_id=lookup_id,
            keys=keys,
            pin=pin,
            priority=Priorities.PEEK,
        )

    def support_batched_get_non_blocking(self) -> bool:
        return True

    async def _batched_get_non_blocking(
        self,
        lookup_id: str,
        keys: List[CacheEngineKey],
    ) -> List[MemoryObj]:
        """Batched get with MGET for efficient multi-key retrieval"""
        if not keys:
            return []

        results = []

        # Process in chunks to avoid overwhelming Redis
        for i in range(0, len(keys), self.chunk_size):
            chunk_keys = keys[i : i + self.chunk_size]

            # Build list of all keys to fetch (metadata and kv for each)
            all_keys = []
            for key in chunk_keys:
                metadata_key, kv_key = self._get_keys(key)
                all_keys.extend([metadata_key, kv_key])

            # Fetch all keys in one MGET
            async with self.sem:
                mget_results = await self.connection.mget(all_keys)

            # Process results in pairs (metadata, kv_bytes)
            for idx, key in enumerate(chunk_keys):
                metadata_bytes = mget_results[idx * 2]
                kv_bytes = mget_results[idx * 2 + 1]

                if metadata_bytes is None:
                    continue

                try:
                    metadata = RemoteMetadata.deserialize(memoryview(metadata_bytes))

                    memory_obj = self.local_cpu_backend.allocate(
                        metadata.shape,
                        metadata.dtype,
                        metadata.fmt,
                    )
                    if memory_obj is None:
                        logger.warning("Failed to allocate memory during remote receive")
                        continue

                    if kv_bytes is None:
                        logger.warning(
                            f"Key {key} exists but KV cache does not exist. "
                            "Might happen when the cache is evicted by redis."
                        )
                        memory_obj.ref_count_down()
                        continue

                    if isinstance(memory_obj.byte_array, memoryview):
                        view = memory_obj.byte_array
                        if view.format == "<B":
                            view = view.cast("B")
                    else:
                        view = memoryview(memory_obj.byte_array)

                    if isinstance(kv_bytes, (bytes, bytearray)):
                        view[: metadata.length] = kv_bytes
                    elif isinstance(kv_bytes, str):
                        converted = kv_bytes.encode("utf-8")
                        view[: metadata.length] = converted
                    else:
                        converted = bytes(kv_bytes)
                        view[: metadata.length] = converted

                    results.append(memory_obj)
                except Exception as exc:
                    logger.error(f"Failed to process key {key}: {exc}")
                    continue

        return results

    async def batched_get_non_blocking(
        self,
        lookup_id: str,
        keys: List[CacheEngineKey],
    ) -> List[MemoryObj]:
        return await self.executor.submit_job(
            self._batched_get_non_blocking,
            lookup_id=lookup_id,
            keys=keys,
            priority=Priorities.PREFETCH,
        )

    @no_type_check
    async def list(self) -> List[str]:
        pass

    async def close(self):
        await self.executor.shutdown(wait=True)
        await self.connection.close()
        logger.info("Closed the redis connection")


class RedisSentinelConnector(RemoteConnector):
    """
    Uses redis.Sentinel to connect to a Redis cluster.
    The hosts are specified in the config file, started with "redis-sentinel://"
    and separated by commas.

    Example:
        remote_url: "redis-sentinel://localhost:26379,localhost:26380,localhost:26381"

    Extra environment variables:
    - REDIS_SERVICE_NAME (required) -- service name for redis.
    - REDIS_TIMEOUT (optional) -- Timeout in seconds, default is 1 if not set
    """

    ENV_REDIS_TIMEOUT = "REDIS_TIMEOUT"
    ENV_REDIS_SERVICE_NAME = "REDIS_SERVICE_NAME"

    def __init__(
        self,
        hosts_and_ports: List[Tuple[str, int]],
        username: str,
        password: str,
        loop: asyncio.AbstractEventLoop,
        local_cpu_backend: LocalCPUBackend,
    ):
        # Get service name
        match os.environ.get(self.ENV_REDIS_SERVICE_NAME):
            case None:
                logger.warning(
                    f"Environment variable {self.ENV_REDIS_SERVICE_NAME} is "
                    f"not found, using default value 'redismaster'"
                )
                service_name = "redismaster"
            case value:
                service_name = value

        timeout: float = -1000.0

        # Get timeout
        match os.environ.get(self.ENV_REDIS_TIMEOUT):
            case None:
                timeout = 1
            case value:
                timeout = float(value)

        logger.info(f"Host and ports: {hosts_and_ports}")
        self.sentinel = redis.Sentinel(hosts_and_ports, socket_timeout=timeout)
        self.master = self.sentinel.master_for(
            service_name, socket_timeout=timeout, username=username, password=password
        )
        self.slave = self.sentinel.slave_for(
            service_name, socket_timeout=timeout, username=username, password=password
        )

        self.local_cpu_backend = local_cpu_backend

    async def exists(self, key: CacheEngineKey) -> bool:
        return bool(self.slave.exists(key.to_string() + "metadata"))

    def exists_sync(self, key: CacheEngineKey) -> bool:
        return bool(self.slave.exists(key.to_string() + "metadata"))

    async def get(self, key: CacheEngineKey) -> Optional[MemoryObj]:
        key_str = key.to_string()
        metadata_bytes = self.slave.get(key_str + "metadata")

        if metadata_bytes is None:
            return None

        assert not inspect.isawaitable(metadata_bytes)

        metadata = RemoteMetadata.deserialize(metadata_bytes)

        memory_obj = self.local_cpu_backend.allocate(
            metadata.shape,
            metadata.dtype,
            metadata.fmt,
        )
        if memory_obj is None:
            logger.warning("Failed to allocate memory during remote receive")
            return None

        kv_bytes = self.slave.get(key_str + "kv_bytes")

        assert not inspect.isawaitable(kv_bytes)

        if kv_bytes is None:
            logger.warning(
                "Key exists but KV cache does not exist."
                "Might happen when the cache is evicted by redis."
            )
            self.master.delete(key_str + "metadata")
            return None

        if isinstance(memory_obj.byte_array, memoryview):
            view = memory_obj.byte_array
            if view.format == "<B":
                view = view.cast("B")
        else:
            view = memoryview(memory_obj.byte_array)

        if isinstance(kv_bytes, (bytes, bytearray)):
            view[0 : metadata.length] = kv_bytes
        elif isinstance(kv_bytes, str):
            converted = kv_bytes.encode("utf-8")
            view[0 : metadata.length] = converted
        else:
            converted = bytes(kv_bytes)
            view[0 : metadata.length] = converted

        return memory_obj

    async def put(self, key: CacheEngineKey, memory_obj: MemoryObj):
        kv_bytes = memory_obj.byte_array
        kv_shape = memory_obj.get_shape()
        kv_dtype = memory_obj.get_dtype()
        memory_format = memory_obj.get_memory_format()

        metadata_bytes = RemoteMetadata(
            len(kv_bytes), kv_shape, kv_dtype, memory_format
        ).serialize()

        key_str = key.to_string()
        self.master.set(key_str + "metadata", metadata_bytes)
        self.master.set(key_str + "kv_bytes", kv_bytes)

        memory_obj.ref_count_down()

    @no_type_check
    async def list(self) -> List[str]:
        pass

    async def close(self):
        self.master.close()
        self.slave.close()


class RedisClusterConnector(RemoteConnector):
    """
    Enhanced Redis Cluster connector with hash-slot aware batching and pipelining.

    Features:
    - Hash tags for co-locating metadata and kv_bytes on same slot
    - Slot-aware batching for efficient multi-key operations
    - Pipelined MGET for reads grouped by slot
    - Pipelined SET for writes grouped by slot
    - Configurable chunk sizes
    """

    def __init__(
        self,
        hosts_and_ports: List[Tuple[str, int]],
        username: str,
        password: str,
        loop: asyncio.AbstractEventLoop,
        local_cpu_backend: LocalCPUBackend,
        chunk_size: int = 256,
        max_connections: int = 150,
        use_tls: bool = False,
    ):
        # Convert hosts_and_ports to startup_nodes format
        startup_nodes = [ClusterNode(h, p) for (h, p) in hosts_and_ports]

        self.chunk_size = chunk_size
        self.max_connections = max_connections
        self.use_tls = use_tls
        self.sem = asyncio.Semaphore(max_connections)

        # Initialize cluster connection
        self.cluster = self._init_connection(startup_nodes, username, password)
        self.loop = loop
        self.local_cpu_backend = local_cpu_backend
        self.executor = AsyncPQExecutor(loop)

    def _init_connection(
        self, startup_nodes: List[ClusterNode], username: str, password: str
    ):
        """Initialize RedisCluster connection"""

        async def create_connection():
            try:
                cluster_kwargs = {
                    "startup_nodes": startup_nodes,
                    "username": username if username else None,
                    "password": password if password else None,
                    "max_connections": self.max_connections,
                    "decode_responses": False,
                }
                if self.use_tls:
                    cluster_kwargs["ssl"] = True
                    cluster_kwargs["ssl_cert_reqs"] = None  # Don't verify certs

                return await RedisCluster(**cluster_kwargs)
            except Exception as e:
                raise RuntimeError(f"Failed to init redis cluster: {e}") from e

        future = asyncio.run_coroutine_threadsafe(create_connection(), self.loop)
        connection = future.result(timeout=10.0)
        return connection

    def _get_keys_with_hash_tag(self, key: CacheEngineKey) -> Tuple[str, str]:
        """
        Generate metadata and kv_bytes keys with hash tag for same slot placement.

        Using hash tags {key} ensures both metadata and kv_bytes are on the same slot,
        enabling single-slot operations and reducing round-trips.
        """
        key_str = key.to_string()
        # Use hash tag to ensure both keys go to same slot
        metadata_key = f"{{{key_str}}}:metadata"
        kv_key = f"{{{key_str}}}:kv_bytes"
        return metadata_key, kv_key

    def _group_keys_by_slot(
        self, keys: List[str]
    ) -> Dict[int, List[Tuple[int, str]]]:
        """
        Group keys by their hash slot.

        Returns a dict mapping slot -> list of (original_index, key) tuples.
        This allows us to preserve order when reconstructing results.
        """
        slot_groups: Dict[int, List[Tuple[int, str]]] = defaultdict(list)

        for idx, key in enumerate(keys):
            # Get the slot for this key
            slot = self.cluster.keyslot(key)
            slot_groups[slot].append((idx, key))

        return slot_groups

    async def _exists(self, key: CacheEngineKey) -> bool:
        metadata_key, _ = self._get_keys_with_hash_tag(key)
        async with self.sem:
            return bool(await self.cluster.exists(metadata_key))

    async def exists(self, key: CacheEngineKey) -> bool:
        return await self.executor.submit_job(
            self._exists, key=key, priority=Priorities.PEEK
        )

    def exists_sync(self, key: CacheEngineKey) -> bool:
        future = asyncio.run_coroutine_threadsafe(
            self.executor.submit_job(self._exists, key=key, priority=Priorities.PEEK),
            self.loop,
        )
        return bool(future.result())

    async def _get(self, key: CacheEngineKey) -> Optional[MemoryObj]:
        """Get a single key using MGET"""
        metadata_key, kv_key = self._get_keys_with_hash_tag(key)

        async with self.sem:
            # Both keys have same hash tag, so they're on same slot
            results = await self.cluster.mget([metadata_key, kv_key])

        if len(results) != 2:
            return None

        metadata_bytes, kv_bytes = results[0], results[1]

        if metadata_bytes is None:
            return None

        assert not inspect.isawaitable(metadata_bytes)

        metadata = RemoteMetadata.deserialize(memoryview(metadata_bytes))

        memory_obj = self.local_cpu_backend.allocate(
            metadata.shape,
            metadata.dtype,
            metadata.fmt,
        )
        if memory_obj is None:
            logger.warning("Failed to allocate memory during remote receive")
            return None

        if kv_bytes is None:
            logger.warning(
                "Key exists but KV cache does not exist. "
                "Might happen when the cache is evicted by redis."
            )
            memory_obj.ref_count_down()
            return None

        assert not inspect.isawaitable(kv_bytes)

        try:
            if isinstance(memory_obj.byte_array, memoryview):
                view = memory_obj.byte_array
                if view.format == "<B":
                    view = view.cast("B")
            else:
                view = memoryview(memory_obj.byte_array)

            if isinstance(kv_bytes, (bytes, bytearray)):
                view[: metadata.length] = kv_bytes
            elif isinstance(kv_bytes, str):
                converted = kv_bytes.encode("utf-8")
                view[: metadata.length] = converted
            else:
                converted = bytes(kv_bytes)
                view[: metadata.length] = converted
            return memory_obj
        except Exception as exc:
            logger.error(f"Failed to convert data: {exc}")
            return None

    async def get(self, key: CacheEngineKey) -> Optional[MemoryObj]:
        return await self.executor.submit_job(
            self._get, key=key, priority=Priorities.GET
        )

    async def _put(self, key: CacheEngineKey, memory_obj: MemoryObj):
        """Put using pipeline"""
        try:
            kv_bytes = bytes(memory_obj.byte_array)
            kv_shape = memory_obj.get_shape()
            kv_dtype = memory_obj.get_dtype()
            memory_format = memory_obj.get_memory_format()

            metadata_bytes = RemoteMetadata(
                len(kv_bytes), kv_shape, kv_dtype, memory_format
            ).serialize()

            metadata_key, kv_key = self._get_keys_with_hash_tag(key)

            # Use pipeline - both keys on same slot due to hash tag
            async with self.sem:
                pipe = self.cluster.pipeline()
                pipe.set(kv_key, kv_bytes)
                pipe.set(metadata_key, metadata_bytes)
                await pipe.execute()

        except Exception as exc:
            logger.error(f"Failed to put data: {exc}")

    async def put(self, key: CacheEngineKey, memory_obj: MemoryObj):
        await self.executor.submit_job(
            self._put, key=key, memory_obj=memory_obj, priority=Priorities.PUT
        )

    def support_batched_put(self) -> bool:
        return True

    async def _batched_put(
        self, keys: List[CacheEngineKey], memory_objs: List[MemoryObj]
    ):
        """
        Batched put with slot-aware pipelining.

        Groups keys by slot, then pipelines operations per slot group.
        """
        try:
            # Group keys by slot
            key_strs = [k.to_string() for k in keys]
            metadata_keys = [f"{{{k}}}:metadata" for k in key_strs]
            slot_groups = self._group_keys_by_slot(metadata_keys)

            # Create a mapping from key to memory_obj for quick lookup
            key_to_obj = {
                keys[i]: memory_objs[i] for i in range(len(keys))
            }

            # Process each slot group with pipelining
            tasks = []
            for slot, key_list in slot_groups.items():
                async def process_slot(key_list):
                    async with self.sem:
                        pipe = self.cluster.pipeline()
                        for orig_idx, metadata_key in key_list:
                            key = keys[orig_idx]
                            memory_obj = key_to_obj[key]

                            kv_bytes = bytes(memory_obj.byte_array)
                            kv_shape = memory_obj.get_shape()
                            kv_dtype = memory_obj.get_dtype()
                            memory_format = memory_obj.get_memory_format()

                            metadata_bytes = RemoteMetadata(
                                len(kv_bytes), kv_shape, kv_dtype, memory_format
                            ).serialize()

                            _, kv_key = self._get_keys_with_hash_tag(key)

                            pipe.set(kv_key, kv_bytes)
                            pipe.set(metadata_key, metadata_bytes)

                        await pipe.execute()

                tasks.append(process_slot(key_list))

            # Execute all slot groups in parallel
            await asyncio.gather(*tasks)

        except Exception as exc:
            logger.error(f"Failed to batched put: {exc}")

    async def batched_put(
        self, keys: List[CacheEngineKey], memory_objs: List[MemoryObj]
    ):
        await self.executor.submit_job(
            self._batched_put,
            keys=keys,
            memory_objs=memory_objs,
            priority=Priorities.PUT,
        )

    def support_batched_async_contains(self) -> bool:
        return True

    async def _batched_async_contains(
        self,
        lookup_id: str,
        keys: List[CacheEngineKey],
        pin: bool = False,
    ) -> int:
        """Batched exists check with slot awareness"""
        metadata_keys = [self._get_keys_with_hash_tag(key)[0] for key in keys]

        # Group by slot
        slot_groups = self._group_keys_by_slot(metadata_keys)

        # Check existence per slot
        count = 0
        for slot, key_list in slot_groups.items():
            keys_to_check = [k for _, k in key_list]
            async with self.sem:
                result = await self.cluster.exists(*keys_to_check)
                count += result

        return count

    async def batched_async_contains(
        self,
        lookup_id: str,
        keys: List[CacheEngineKey],
        pin: bool = False,
    ) -> int:
        return await self.executor.submit_job(
            self._batched_async_contains,
            lookup_id=lookup_id,
            keys=keys,
            pin=pin,
            priority=Priorities.PEEK,
        )

    def support_batched_get_non_blocking(self) -> bool:
        return True

    async def _batched_get_non_blocking(
        self,
        lookup_id: str,
        keys: List[CacheEngineKey],
    ) -> List[MemoryObj]:
        """
        Slot-aware batched get with MGET pipelining.

        Groups keys by slot, issues MGET per slot, preserves order.
        """
        if not keys:
            return []

        # Build all keys and group by slot
        all_key_info = []
        for idx, key in enumerate(keys):
            metadata_key, kv_key = self._get_keys_with_hash_tag(key)
            all_key_info.append((idx, key, metadata_key, kv_key))

        # Group metadata keys by slot
        metadata_keys = [info[2] for info in all_key_info]
        slot_groups = self._group_keys_by_slot(metadata_keys)

        # Results array to maintain order
        results_by_index: Dict[int, Optional[MemoryObj]] = {}

        # Process each slot group
        for slot, key_list in slot_groups.items():
            # Get all keys for this slot (metadata and kv for each original key)
            slot_keys = []
            slot_indices = []
            for orig_idx, _ in key_list:
                info = all_key_info[orig_idx]
                idx, key, metadata_key, kv_key = info
                slot_keys.extend([metadata_key, kv_key])
                slot_indices.append(idx)

            # Fetch all keys for this slot with MGET
            async with self.sem:
                mget_results = await self.cluster.mget(slot_keys)

            # Process results for this slot
            for i, orig_idx in enumerate(slot_indices):
                metadata_bytes = mget_results[i * 2]
                kv_bytes = mget_results[i * 2 + 1]
                key = keys[orig_idx]

                if metadata_bytes is None:
                    results_by_index[orig_idx] = None
                    continue

                try:
                    metadata = RemoteMetadata.deserialize(memoryview(metadata_bytes))

                    memory_obj = self.local_cpu_backend.allocate(
                        metadata.shape,
                        metadata.dtype,
                        metadata.fmt,
                    )
                    if memory_obj is None:
                        logger.warning("Failed to allocate memory during remote receive")
                        results_by_index[orig_idx] = None
                        continue

                    if kv_bytes is None:
                        logger.warning(
                            f"Key {key} exists but KV cache does not exist. "
                            "Might happen when the cache is evicted by redis."
                        )
                        memory_obj.ref_count_down()
                        results_by_index[orig_idx] = None
                        continue

                    if isinstance(memory_obj.byte_array, memoryview):
                        view = memory_obj.byte_array
                        if view.format == "<B":
                            view = view.cast("B")
                    else:
                        view = memoryview(memory_obj.byte_array)

                    if isinstance(kv_bytes, (bytes, bytearray)):
                        view[: metadata.length] = kv_bytes
                    elif isinstance(kv_bytes, str):
                        converted = kv_bytes.encode("utf-8")
                        view[: metadata.length] = converted
                    else:
                        converted = bytes(kv_bytes)
                        view[: metadata.length] = converted

                    results_by_index[orig_idx] = memory_obj
                except Exception as exc:
                    logger.error(f"Failed to process key {key}: {exc}")
                    results_by_index[orig_idx] = None

        # Return results in original order, filtering None
        ordered_results = []
        for idx in range(len(keys)):
            if idx in results_by_index and results_by_index[idx] is not None:
                ordered_results.append(results_by_index[idx])

        return ordered_results

    async def batched_get_non_blocking(
        self,
        lookup_id: str,
        keys: List[CacheEngineKey],
    ) -> List[MemoryObj]:
        return await self.executor.submit_job(
            self._batched_get_non_blocking,
            lookup_id=lookup_id,
            keys=keys,
            priority=Priorities.PREFETCH,
        )

    @no_type_check
    async def list(self) -> List[str]:
        pass

    async def close(self):
        await self.executor.shutdown(wait=True)
        await self.cluster.close()
        logger.info("Closed the redis cluster connection")
