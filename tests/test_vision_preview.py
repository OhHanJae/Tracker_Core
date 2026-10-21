import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.communication.json_tcp import JsonLineServer, JsonTcpResponse
from src.core.web_application import CoreWebApplication


class VisionPreviewTest(unittest.TestCase):
    def settings(self):
        return SimpleNamespace(web_enabled=True, web_host="192.168.0.20", web_port=8767)

    def test_reads_only_configured_preview_and_copies_numeric_headers(self):
        response = Mock(status=200)
        response.read.return_value = b"\xff\xd8jpeg\xff\xd9"
        response.getheader.side_effect = lambda name, default="": {
            "Content-Type": "image/jpeg", "X-Frame-Id": "42", "X-Source-Width": "2448",
            "X-Source-Height": "bad\r\nInjected: 1",
        }.get(name, default)
        with patch("src.core.web_application.http.client.HTTPConnection") as factory:
            connection = factory.return_value
            connection.getresponse.return_value = response
            status, body, content_type, headers = CoreWebApplication._read_vision_frame(self.settings(), "41")
            factory.assert_called_once_with("192.168.0.20", 8767, timeout=1.5)
            connection.request.assert_called_once_with("GET", "/api/camera/live.jpg?after=41", headers={"Accept": "image/jpeg"})
            response.read.assert_called_once_with(4 * 1024 * 1024 + 1)
            connection.close.assert_called_once()
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, response.read.return_value)
        self.assertEqual(content_type, "image/jpeg")
        self.assertEqual(headers, {"X-Frame-Id": "42", "X-Source-Width": "2448"})

    def test_no_frame_503_redirect_invalid_type_and_oversize_are_bounded(self):
        for source_status, content_type, content, expected in (
            (204, "image/jpeg", b"", "204"), (503, "text/plain", b"missing", "503"),
            (302, "text/html", b"redirect", "502"), (200, "text/html", b"html", "502"),
            (200, "image/jpeg", b"x" * (4 * 1024 * 1024 + 1), "502"),
        ):
            with self.subTest(source_status=source_status, content_type=content_type, size=len(content)):
                response = Mock(status=source_status)
                response.getheader.side_effect = lambda name, default="": content_type if name == "Content-Type" else default
                response.read.return_value = content
                with patch("src.core.web_application.http.client.HTTPConnection") as factory:
                    factory.return_value.getresponse.return_value = response
                    result = CoreWebApplication._read_vision_frame(self.settings(), "")
                    self.assertTrue(result[0].startswith(expected))
                    factory.return_value.close.assert_called_once()

    def test_disabled_or_invalid_request_does_not_connect(self):
        async def run():
            app = object.__new__(CoreWebApplication)
            app.settings = SimpleNamespace(vision=self.settings())
            app._vision_frame_slots = asyncio.Semaphore(4)
            with patch.object(app, "_read_vision_frame") as read:
                for after in ("../api/config", "1&view=other", "-1", "9" * 21):
                    response = await app._monitor_vision_frame({"after": after})
                    self.assertTrue(response[0].startswith("400"))
                app.settings.vision.web_enabled = False
                response = await app._monitor_vision_frame({})
                self.assertTrue(response[0].startswith("503"))
                read.assert_not_called()
        asyncio.run(run())

    def test_preview_io_runs_off_loop_and_cancellation_retains_slot(self):
        async def run():
            app = object.__new__(CoreWebApplication)
            app.settings = SimpleNamespace(vision=self.settings())
            app._vision_frame_slots = asyncio.Semaphore(1)
            entered = threading.Event()
            release = threading.Event()
            loop_thread = threading.get_ident()
            def read(*_args):
                self.assertNotEqual(threading.get_ident(), loop_thread)
                entered.set()
                release.wait(2)
                return "204 No Content", b"", "image/jpeg", {}
            with patch.object(app, "_read_vision_frame", side_effect=read):
                task = asyncio.create_task(app._monitor_vision_frame({}))
                try:
                    for _ in range(100):
                        if entered.is_set():
                            break
                        await asyncio.sleep(.005)
                    self.assertTrue(entered.is_set())
                    task.cancel()
                    await asyncio.sleep(.01)
                    self.assertTrue(app._vision_frame_slots.locked())
                    busy = await app._monitor_vision_frame({})
                    self.assertTrue(busy[0].startswith("503"))
                finally:
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertFalse(app._vision_frame_slots.locked())
        asyncio.run(run())

    def test_binary_http_route_preserves_frame_id_and_no_store(self):
        async def run():
            async def handler(command, params):
                return JsonTcpResponse(True, {})
            server = JsonLineServer("127.0.0.1", 0, handler)
            async def preview(query):
                self.assertEqual(query, {"after": "7"})
                return "200 OK", b"jpeg", "image/jpeg", {"X-Frame-Id": "8"}
            server.vision_frame_handler = preview
            await server.start()
            try:
                port = server._server.sockets[0].getsockname()[1]
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    writer.write(b"GET /api/monitor/vision/frame?after=7 HTTP/1.1\r\nHost: localhost\r\n\r\n")
                    await writer.drain()
                    response = await asyncio.wait_for(reader.read(), 2)
                finally:
                    writer.close()
                    await writer.wait_closed()
                self.assertIn(b"HTTP/1.1 200 OK", response)
                self.assertIn(b"Content-Type: image/jpeg", response)
                self.assertIn(b"X-Frame-Id: 8", response)
                self.assertIn(b"Cache-Control: no-store", response)
                self.assertTrue(response.endswith(b"\r\n\r\njpeg"))
            finally:
                await server.stop()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
