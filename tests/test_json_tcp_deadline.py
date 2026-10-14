"""A peer cannot extend the configured request budget with unrelated lines."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.communication.json_tcp import JsonTcpError, send_json_request
from tests.support import LoopbackPeer


class JsonTcpDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def peer(self, reply):
        peer = await LoopbackPeer(reply).start()
        self.addAsyncCleanup(peer.stop)
        return peer

    async def test_other_ids_and_service_banner_are_ignored_until_matching_response(self):
        async def reply(request, writer):
            for message in ({"event": "ready"}, {"id": "other", "ok": True, "result": 123}):
                writer.write((json.dumps(message) + "\n").encode())
            return {"id": request["id"], "ok": True, "result": {"accepted": True}}
        peer = await self.peer(reply)
        response = await send_json_request("127.0.0.1", peer.port, "tracking.start", {}, 0.2)
        self.assertTrue(response.ok)
        self.assertEqual(response.result, {"accepted": True})

    async def test_repeated_unmatched_ids_have_one_total_deadline(self):
        async def chatter(request, writer):
            while True:
                writer.write(b'{"id":"unrelated","ok":true}\n')
                await writer.drain()
                await asyncio.sleep(0.01)
        peer = await self.peer(chatter)
        started = asyncio.get_running_loop().time()
        with self.assertRaises(JsonTcpError) as caught:
            await asyncio.wait_for(send_json_request("127.0.0.1", peer.port, "vision.result", {}, 0.12), 0.6)
        self.assertIn("timeout", str(caught.exception))
        self.assertEqual(caught.exception.code, "TIMEOUT")
        self.assertLess(asyncio.get_running_loop().time() - started, 0.5)

    async def test_repeated_invalid_json_has_one_total_deadline(self):
        async def chatter(request, writer):
            while True:
                writer.write(b'{invalid-json}\n')
                await writer.drain()
                await asyncio.sleep(0.01)
        peer = await self.peer(chatter)
        with self.assertRaises(JsonTcpError) as caught:
            await asyncio.wait_for(send_json_request("127.0.0.1", peer.port, "vision.result", {}, 0.12), 0.6)
        self.assertEqual(caught.exception.code, "TIMEOUT")

    async def test_silent_peer_times_out_with_stable_error_code(self):
        async def silent(request, writer):
            await asyncio.Event().wait()
        peer = await self.peer(silent)
        with self.assertRaises(JsonTcpError) as caught:
            await send_json_request("127.0.0.1", peer.port, "vision.result", {}, 0.12)
        self.assertEqual(caught.exception.code, "TIMEOUT")

    async def test_nonobject_json_is_ignored_before_matching_response(self):
        async def reply(request, writer):
            writer.write(b'[]\nnull\n"text"\n')
            return {"id": request["id"], "ok": True, "result": {}}
        peer = await self.peer(reply)
        response = await send_json_request("127.0.0.1", peer.port, "vision.status", {}, 0.2)
        self.assertTrue(response.ok)

    async def test_nonboolean_ok_is_not_treated_as_an_error_response(self):
        async def reply(request, writer):
            return {"id": request["id"], "ok": "true", "result": {}}
        peer = await self.peer(reply)
        with self.assertRaises(JsonTcpError) as caught:
            await send_json_request("127.0.0.1", peer.port, "vision.status", {}, 0.2)
        self.assertEqual(caught.exception.code, "INVALID_RESPONSE")

    async def test_connection_closed_before_response_is_controlled_error(self):
        async def close(request, writer):
            return None
        peer = await self.peer(close)
        with self.assertRaisesRegex(JsonTcpError, "connection closed"):
            await send_json_request("127.0.0.1", peer.port, "vision.result", {}, 0.2)

    async def test_connect_failure_preserves_controlled_error(self):
        with patch("src.communication.json_tcp.asyncio.open_connection", new=AsyncMock(side_effect=ConnectionRefusedError("test"))):
            with self.assertRaises(JsonTcpError) as caught:
                await send_json_request("127.0.0.1", 8768, "vision.result", {}, 0.12)
        self.assertEqual(caught.exception.code, "CONNECT_FAILED")

    async def test_connect_timeout_is_not_misclassified_as_oserror(self):
        async def blocked(*args):
            await asyncio.Event().wait()
        with patch("src.communication.json_tcp.asyncio.open_connection", new=AsyncMock(side_effect=blocked)):
            with self.assertRaises(JsonTcpError) as caught:
                await send_json_request("127.0.0.1", 8768, "vision.result", {}, 0.03)
        self.assertEqual(caught.exception.code, "TIMEOUT")
        self.assertIn("connect timeout", str(caught.exception))

    async def test_connect_send_and_read_share_the_same_budget(self):
        # Each phase fits separately; their sum exceeds the request budget.
        reader, writer = MagicMock(), MagicMock()
        async def read():
            await asyncio.sleep(0.06)
            return b'{"id":"budget","ok":true,"result":{}}\n'
        async def drain():
            await asyncio.sleep(0.06)
        async def connect(*args):
            await asyncio.sleep(0.06)
            return reader, writer
        reader.readline = AsyncMock(side_effect=read)
        writer.drain = AsyncMock(side_effect=drain)
        writer.wait_closed = AsyncMock()
        with patch("src.communication.json_tcp.asyncio.open_connection", new=AsyncMock(side_effect=connect)):
            with self.assertRaises(JsonTcpError) as caught:
                await send_json_request("127.0.0.1", 8768, "vision.result", {}, 0.15, request_id="budget")
        self.assertEqual(caught.exception.code, "TIMEOUT")
        writer.close.assert_called_once()

    async def test_json_without_newline_is_not_a_json_lines_response(self):
        async def reply(request, writer):
            writer.write(json.dumps({"id": request["id"], "ok": True, "result": {}}).encode())
            await writer.drain()
        peer = await self.peer(reply)
        with self.assertRaises(JsonTcpError) as caught:
            await send_json_request("127.0.0.1", peer.port, "vision.result", {}, 0.2)
        self.assertEqual(caught.exception.code, "INVALID_RESPONSE")
