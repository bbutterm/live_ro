"""Unix-сокет для плагина OpenKore brainBridge: JSON-строки в обе стороны."""
import asyncio
import json
import logging
import os

log = logging.getLogger("bridge")


class Bridge:
    def __init__(self, path, on_message):
        self.path = path
        self.on_message = on_message
        self.writer = None
        self.server = None
        self.next_id = 1

    @property
    def connected(self):
        return self.writer is not None and not self.writer.is_closing()

    async def start(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        if os.path.exists(self.path):
            os.unlink(self.path)
        self.server = await asyncio.start_unix_server(self._client, path=self.path)
        os.chmod(self.path, 0o600)
        log.info("жду плагин brainBridge на %s", self.path)

    async def close(self):
        if self.writer:
            self.writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if os.path.exists(self.path):
            os.unlink(self.path)

    async def _client(self, reader, writer):
        if self.connected:
            log.warning("новое подключение плагина заменяет старое")
            self.writer.close()
        self.writer = writer
        log.info("плагин brainBridge подключился")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("не JSON от плагина: %r", line[:200])
                    continue
                if isinstance(msg, dict):
                    await self.on_message(msg)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if self.writer is writer:
                self.writer = None
            log.info("плагин brainBridge отключился")

    async def send_action(self, action):
        if not self.connected:
            return None
        msg = dict(action, type="action", id=self.next_id)
        self.next_id += 1
        self.writer.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.writer.drain()
        return msg["id"]
