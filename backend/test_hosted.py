import asyncio
from base64 import b64encode
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from .hosted import create_app


async def request(app, path, authorization=None, method="GET"):
    events = []
    headers = [] if authorization is None else [(b"authorization", authorization)]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": method, "scheme": "https", "path": path,
             "raw_path": path.encode(), "query_string": b"", "headers": headers,
             "client": ("127.0.0.1", 4000), "server": ("localhost", 443), "root_path": ""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(event):
        events.append(event)

    await asyncio.wait_for(app(scope, receive, send), timeout=3)
    return events[0]["status"], dict(events[0]["headers"])


class HostedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        settings = patch.dict(os.environ, {"INVAPLAN_RELEASE_DIR": directory.name,
                                           "INVAPLAN_AREA_DIR": directory.name}, clear=True)
        settings.start()
        self.addCleanup(settings.stop)

    async def test_missing_credentials_and_invalid_modes_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "PASSWORD"):
            create_app()
        os.environ["INVAPLAN_ACCESS_MODE"] = "publc"
        with self.assertRaisesRegex(ValueError, "ACCESS_MODE"):
            create_app()

    async def test_private_gate_precedes_all_research_routes(self):
        password = "test-only"
        os.environ["INVAPLAN_REVIEW_PASSWORD"] = password
        auth = b"Basic " + b64encode(("reviewer:" + password).encode())
        app = create_app()
        self.assertEqual((await request(app, "/healthz"))[0], 204)
        self.assertEqual((await request(app, "/", auth))[0], 503)
        # Exercise normal HTTP routing with the disk present. No real research files are required.
        (self.root / "area-cities.json").touch()
        app = create_app()
        for path in ("/", "/assets/test.js", "/api/catalog", "/docs", "/openapi.json",
                     "/api/area-comparisons/footprints", "/api/layers/test.png"):
            status, headers = await request(app, path)
            self.assertEqual(status, 401, path)
            self.assertIn(b"Basic", headers[b"www-authenticate"])
        self.assertEqual((await request(app, "/api/runs", method="POST"))[0], 401)
        self.assertEqual((await request(app, "/docs", b"Basic incorrect"))[0], 401)
        status, headers = await request(app, "/docs", auth)
        self.assertEqual(status, 200)
        self.assertIn(b"no-store", headers[b"cache-control"])

    async def test_public_mode_allows_anonymous_visits(self):
        os.environ["INVAPLAN_ACCESS_MODE"] = "public"
        status, headers = await request(create_app(), "/docs")
        self.assertEqual(status, 200)
        self.assertNotIn(b"www-authenticate", headers)
