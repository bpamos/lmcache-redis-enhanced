# SPDX-License-Identifier: Apache-2.0
# Standard
from typing import List, Tuple

# First Party
from lmcache.logging import init_logger
from lmcache.v1.storage_backend.connector import (
    ConnectorAdapter,
    ConnectorContext,
    parse_remote_url,
)
from lmcache.v1.storage_backend.connector.base_connector import RemoteConnector

logger = init_logger(__name__)


class RedisConnectorAdapter(ConnectorAdapter):
    """Adapter for Redis Server connectors with batching/pipelining support."""

    def __init__(self) -> None:
        super().__init__("redis://")

    def can_parse(self, url: str) -> bool:
        return url.startswith((self.schema, "rediss://", "unix://"))

    def create_connector(self, context: ConnectorContext) -> RemoteConnector:
        # Local
        from .redis_connector import RedisClusterConnector, RedisConnector

        config = context.config

        # Read configuration from extra_config, mirroring Valkey's approach
        if config is not None and config.extra_config is not None:
            self.redis_username = config.extra_config.get("redis_username", "")
            self.redis_password = config.extra_config.get("redis_password", "")
            self.redis_database = config.extra_config.get("redis_database", None)
            self.redis_mode = config.extra_config.get("redis_mode", "standalone")
            self.chunk_size = config.extra_config.get("chunk_size", 256)
            self.max_connections = config.extra_config.get("max_connections", 150)
            self.use_tls = config.extra_config.get("use_tls", False)
        else:
            self.redis_username = ""
            self.redis_password = ""
            self.redis_database = None
            self.redis_mode = "standalone"
            self.chunk_size = 256
            self.max_connections = 150
            self.use_tls = False

        # Auto-detect TLS from URL scheme
        if context.url.startswith("rediss://"):
            self.use_tls = True

        logger.info(f"Creating Redis connector for URL: {context.url}")
        logger.info(f"Redis mode: {self.redis_mode}, chunk_size: {self.chunk_size}")

        if self.redis_mode == "cluster":
            # Parse multiple hosts for cluster mode
            hosts_and_ports: List[Tuple[str, int]] = []
            assert self.schema is not None
            for sub_url in context.url.split(","):
                if not sub_url.startswith(self.schema):
                    sub_url = self.schema + sub_url

                parsed_url = parse_remote_url(sub_url)
                hosts_and_ports.append((parsed_url.host, parsed_url.port))

            return RedisClusterConnector(
                hosts_and_ports=hosts_and_ports,
                loop=context.loop,
                local_cpu_backend=context.local_cpu_backend,
                username=self.redis_username,
                password=self.redis_password,
                chunk_size=self.chunk_size,
                max_connections=self.max_connections,
                use_tls=self.use_tls,
            )
        else:
            # Standalone mode
            url = context.url
            return RedisConnector(
                url=url,
                loop=context.loop,
                local_cpu_backend=context.local_cpu_backend,
                username=self.redis_username,
                password=self.redis_password,
                database_id=self.redis_database,
                chunk_size=self.chunk_size,
                max_connections=self.max_connections,
                use_tls=self.use_tls,
            )


class RedisSentinelConnectorAdapter(ConnectorAdapter):
    """Adapter for Redis Sentinel connectors."""

    def __init__(self) -> None:
        super().__init__("redis-sentinel://")

    def create_connector(self, context: ConnectorContext) -> RemoteConnector:
        # Local
        from .redis_connector import RedisSentinelConnector

        logger.info(f"Creating Redis Sentinel connector for URL: {context.url}")
        url = context.url[len(self.schema) :]

        # Parse username and password
        username: str = ""
        password: str = ""
        if "@" in url:
            auth, url = url.split("@", 1)
            if ":" in auth:
                username, password = auth.split(":", 1)
            else:
                username = auth

        # Parse host and port
        hosts_and_ports: List[Tuple[str, int]] = []
        assert self.schema is not None
        for sub_url in url.split(","):
            if not sub_url.startswith(self.schema):
                sub_url = self.schema + sub_url

            parsed_url = parse_remote_url(sub_url)
            hosts_and_ports.append((parsed_url.host, parsed_url.port))

        return RedisSentinelConnector(
            hosts_and_ports=hosts_and_ports,
            username=username,
            password=password,
            loop=context.loop,
            local_cpu_backend=context.local_cpu_backend,
        )


class RedisClusterConnectorAdapter(ConnectorAdapter):
    """
    Adapter for Redis Cluster connectors.
    DEPRECATED: Use RedisConnectorAdapter with redis_mode="cluster" instead.
    """

    def __init__(self) -> None:
        super().__init__("redis-cluster://")

    def can_parse(self, url: str) -> bool:
        return url.startswith(self.schema)

    def create_connector(self, context: ConnectorContext) -> RemoteConnector:
        # Local
        from .redis_connector import RedisClusterConnector

        logger.warning(
            "redis-cluster:// URL scheme is deprecated. "
            "Use redis:// with redis_mode='cluster' instead."
        )
        logger.info(f"Creating Redis Cluster connector for URL: {context.url}")
        url = context.url[len(self.schema) :]

        # Parse username and password
        username: str = ""
        password: str = ""
        if "@" in url:
            auth, url = url.split("@", 1)
            if ":" in auth:
                username, password = auth.split(":", 1)
            else:
                username = auth

        # Parse host and port
        hosts_and_ports: List[Tuple[str, int]] = []
        assert self.schema is not None
        for sub_url in url.split(","):
            if not sub_url.startswith(self.schema):
                sub_url = self.schema + sub_url

            parsed_url = parse_remote_url(sub_url)
            hosts_and_ports.append((parsed_url.host, parsed_url.port))

        config = context.config
        chunk_size = 256
        max_connections = 150
        if config is not None and config.extra_config is not None:
            chunk_size = config.extra_config.get("chunk_size", 256)
            max_connections = config.extra_config.get("max_connections", 150)

        return RedisClusterConnector(
            hosts_and_ports=hosts_and_ports,
            username=username,
            password=password,
            loop=context.loop,
            local_cpu_backend=context.local_cpu_backend,
            chunk_size=chunk_size,
            max_connections=max_connections,
        )
