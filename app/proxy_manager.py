"""Asyncio TCP listen servers with bidirectional byte forwarding."""

from __future__ import annotations

import asyncio
import logging
from asyncio import Server, StreamReader, StreamWriter
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


async def _pump(reader: StreamReader, writer: StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    except OSError as e:
        logger.debug("pump %s: %s", label, e)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


@dataclass
class ProxyRuntime:
    proxy_id: int
    listen_port: int
    target_host: str
    target_port: int
    server: Server | None = None
    active_connections: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def _handle_client(
        self,
        client_reader: StreamReader,
        client_writer: StreamWriter,
    ) -> None:
        peer = client_writer.get_extra_info("peername")
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(self.target_host, self.target_port),
                timeout=30.0,
            )
        except Exception as e:
            logger.warning(
                "proxy %s upstream %s:%s failed: %s",
                self.proxy_id,
                self.target_host,
                self.target_port,
                e,
            )
            client_writer.close()
            try:
                await client_writer.wait_closed()
            except Exception:
                pass
            return

        async with self._lock:
            self.active_connections += 1
        try:
            t1 = asyncio.create_task(
                _pump(client_reader, remote_writer),
                name=f"p{self.proxy_id}-c2r",
            )
            t2 = asyncio.create_task(
                _pump(remote_reader, client_writer),
                name=f"p{self.proxy_id}-r2c",
            )
            _done, pending = await asyncio.wait(
                {t1, t2}, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()
                try:
                    await p
                except asyncio.CancelledError:
                    pass
        finally:
            async with self._lock:
                self.active_connections = max(0, self.active_connections - 1)
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass
            try:
                remote_writer.close()
                await remote_writer.wait_closed()
            except Exception:
                pass
            logger.debug("proxy %s session closed peer=%s", self.proxy_id, peer)

    async def start(self) -> None:
        if self.server is not None:
            return

        async def handler(
            r: StreamReader, w: StreamWriter
        ) -> None:
            await self._handle_client(r, w)

        self.server = await asyncio.start_server(
            handler,
            host="0.0.0.0",
            port=self.listen_port,
            reuse_address=True,
        )
        sockets = self.server.sockets or []
        for s in sockets:
            logger.info(
                "proxy %s listening on %s -> %s:%s",
                self.proxy_id,
                s.getsockname(),
                self.target_host,
                self.target_port,
            )

    async def stop(self) -> None:
        if self.server is None:
            return
        self.server.close()
        await self.server.wait_closed()
        self.server = None


class ProxyManager:
    """In-memory registry of running TCP proxies keyed by database id."""

    def __init__(self) -> None:
        self._runtimes: dict[int, ProxyRuntime] = {}
        self._global = asyncio.Lock()

    def snapshot(self) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for pid, rt in self._runtimes.items():
            out[pid] = {
                "running": rt.server is not None,
                "active_connections": rt.active_connections,
                "listen_port": rt.listen_port,
            }
        return out

    async def start_proxy(
        self,
        proxy_id: int,
        listen_port: int,
        target_host: str,
        target_port: int,
    ) -> None:
        async with self._global:
            if proxy_id in self._runtimes:
                rt = self._runtimes[proxy_id]
                if rt.server is not None:
                    return
                await rt.start()
                return
            rt = ProxyRuntime(
                proxy_id=proxy_id,
                listen_port=listen_port,
                target_host=target_host,
                target_port=target_port,
            )
            self._runtimes[proxy_id] = rt
            await rt.start()

    async def stop_proxy(self, proxy_id: int) -> None:
        async with self._global:
            rt = self._runtimes.get(proxy_id)
            if rt is None:
                return
            await rt.stop()

    async def remove_runtime(self, proxy_id: int) -> None:
        async with self._global:
            rt = self._runtimes.pop(proxy_id, None)
            if rt:
                await rt.stop()

    def is_running(self, proxy_id: int) -> bool:
        rt = self._runtimes.get(proxy_id)
        return rt is not None and rt.server is not None
