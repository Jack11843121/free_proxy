"""SOCKS5 本地监听服务器（RFC 1928 / RFC 1929）。

客户端把免费代理地址配成 `socks5h://127.0.0.1:8898`，即可走 free_proxy 转发到池内上游。
上游协议（HTTP / SOCKS5）由代理条目自身的 scheme 决定，链式转发在此透明完成。
"""
from __future__ import annotations

import asyncio
import socket
import struct
from typing import Optional, Tuple

from .config import ServerConfig
from .logger import get_logger
from .tunnel import open_tunnel
from .util import parse_listen

log = get_logger("socks5")

RELAY_CHUNK = 256 * 1024


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(RELAY_CHUNK)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _readexactly(reader: asyncio.StreamReader, n: int) -> bytes:
    try:
        return await reader.readexactly(n)
    except asyncio.IncompleteReadError as e:
        raise ConnectionError("SOCKS5 连接提前关闭") from e


async def _read_addr(reader: asyncio.StreamReader, atyp: int) -> Tuple[str, int]:
    if atyp == 0x01:
        host = socket.inet_ntop(socket.AF_INET, await _readexactly(reader, 4))
    elif atyp == 0x04:
        host = socket.inet_ntop(socket.AF_INET6, await _readexactly(reader, 16))
    elif atyp == 0x03:
        ln = (await _readexactly(reader, 1))[0]
        host = (await _readexactly(reader, ln)).decode("utf-8", "replace")
    else:
        raise ValueError(f"不支持的地址类型 {atyp}")
    port = struct.unpack(">H", await _readexactly(reader, 2))[0]
    return host, port


class Socks5Server:
    def __init__(self, cfg: ServerConfig, pool):
        self.cfg = cfg
        self.pool = pool
        self._server: Optional[asyncio.Server] = None
        self.need_auth = bool(cfg.socks5_user)

    async def start(self) -> None:
        host, port = parse_listen(self.cfg.socks5_listen)
        self._server = await asyncio.start_server(self._handle, host, port, backlog=self.cfg.backlog)
        log.info("SOCKS5 已监听 %s:%d", host, port)

    async def serve_forever(self) -> None:
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _reply(self, writer: asyncio.StreamWriter, rep: int) -> None:
        # 失败回复：BND.ADDR 用 0.0.0.0:0
        writer.write(b"\x05" + bytes([rep]) + b"\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()

    async def _auth(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        """SOCKS5 + RFC1929 用户名密码协商。返回是否通过。"""
        head = await _readexactly(reader, 2)
        if head[0] != 0x05:
            return False
        methods = await _readexactly(reader, head[1])
        if self.need_auth:
            if 0x02 not in methods:
                writer.write(b"\x05\xff")
                await writer.drain()
                return False
            writer.write(b"\x05\x02")
            await writer.drain()
            ver = await _readexactly(reader, 2)
            ulen = ver[1]
            uname = await _readexactly(reader, ulen)
            plen = (await _readexactly(reader, 1))[0]
            passwd = await _readexactly(reader, plen)
            ok = uname.decode() == self.cfg.socks5_user and passwd.decode() == self.cfg.socks5_password
            writer.write(b"\x01\x00" if ok else b"\x01\x01")
            await writer.drain()
            return ok
        if 0x00 not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            return False
        writer.write(b"\x05\x00")
        await writer.drain()
        return True

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if not await self._auth(reader, writer):
                return

            req = await _readexactly(reader, 4)
            if req[0] != 0x05:
                return
            if req[1] != 0x01:  # 仅支持 CONNECT
                await self._reply(writer, 0x07)
                return
            host, port = await _read_addr(reader, req[3])

            # 上游 failover：建隧道失败则换代理重试
            conn = None
            for _ in range(max(1, self.cfg.retry)):
                proxy = self.pool.pick()
                if proxy is None:
                    break
                try:
                    conn = await open_tunnel(proxy, host, port, self.cfg.connect_timeout)
                    break
                except Exception as e:  # noqa: BLE001
                    log.debug("SOCKS5 上游建隧道失败 %s: %s", proxy.key, e)
                    continue
            if conn is None:
                await self._reply(writer, 0x05)  # 连接被拒绝 / 无可用代理
                return

            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            up_reader, up_writer = conn
            await asyncio.gather(_relay(reader, up_writer), _relay(up_reader, writer))
        except (ConnectionError, asyncio.IncompleteReadError, ValueError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass