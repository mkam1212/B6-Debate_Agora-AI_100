from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from email.message import Message
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from server.gizmoapp_server import media


class FakeResponse:
    def __init__(self, body: bytes, content_type: str, status: int = 200):
        self._body = body
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int) -> bytes:
        return self._body[:limit]


class CourseMediaTests(unittest.TestCase):
    def environment(self, operations: str) -> dict[str, str]:
        return {
            "GIZMO_MEDIA_API_KEY": "cwmedia_ws_secret",
            "GIZMO_MEDIA_BASE_URL": "https://course.example/media-proxy/v1",
            "GIZMO_MEDIA_OPERATIONS": operations,
            "GIZMO_MEDIA_TIMEOUT_SECONDS": "12",
        }

    def test_package_helper_import_needs_no_site_packages(self):
        result = subprocess.run([sys.executable, "-S", "-c",
            "import sys; from server.gizmoapp_server import media; assert 'flask' not in sys.modules"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_canonical_img2img_name_and_legacy_alias_both_reach_gateway(self):
        png = media.PNG_SIGNATURE + b"synthetic"
        response = {"data": [{"b64_json": base64.b64encode(png).decode()}]}
        for model in ("stable-diffusion-v1-5-img2img", "stable-diffusion-v1-5"):
            with patch.dict(os.environ, self.environment("image.edit"), clear=True), patch.object(
                media, "urlopen", return_value=FakeResponse(json.dumps(response).encode(), "application/json")
            ) as gateway:
                media.edit_image("Paint this apple", png, model=model)
                self.assertEqual(model, json.loads(gateway.call_args.args[0].data)["model"])

    def test_coding_command_generates_asset_without_flask_or_running_app(self):
        png = media.PNG_SIGNATURE + b"synthetic-direct-command"
        calls = []

        class Gateway(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                calls.append((self.path, self.headers.get("Authorization"),
                              json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                body = json.dumps({"data": [{"b64_json": base64.b64encode(png).decode()}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        gateway = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                environment = dict(os.environ, **self.environment("image.generate"))
                environment["GIZMO_MEDIA_API_KEY"] = "cwmp_temporary-turn-token"
                environment["GIZMO_MEDIA_BASE_URL"] = (
                    f"http://127.0.0.1:{gateway.server_port}/model-proxy/openai/v1"
                )
                command = (
                    "import sys; from pathlib import Path; "
                    "sys.path.insert(0, sys.argv[1]); from media import generate_image; "
                    "assert 'flask' not in sys.modules; "
                    "result = generate_image('A red apple', seed=7); "
                    "output = Path('assets/red-apple.png'); "
                    "output.parent.mkdir(); output.write_bytes(result.data); print(output)"
                )
                result = subprocess.run(
                    [sys.executable, "-I", "-c", command, str(Path(media.__file__).parent)],
                    cwd=tmp, env=environment, capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(png, (Path(tmp) / "assets/red-apple.png").read_bytes())
                self.assertEqual("assets/red-apple.png", result.stdout.strip())
                self.assertNotIn(environment["GIZMO_MEDIA_API_KEY"], result.stdout + result.stderr)
                self.assertEqual(1, len(calls))
                self.assertEqual("/model-proxy/openai/v1/images/generations", calls[0][0])
                self.assertEqual("Bearer cwmp_temporary-turn-token", calls[0][1])
                self.assertEqual("A red apple", calls[0][2]["prompt"])
        finally:
            gateway.shutdown()
            thread.join(timeout=5)
            gateway.server_close()

    def test_generate_image_uses_server_credential_and_returns_png(self):
        png = media.PNG_SIGNATURE + b"prototype"
        response = {
            "data": [{"b64_json": base64.b64encode(png).decode("ascii")}],
            "jobId": "media-0123456789abcdef0123",
            "metadata": {"seed": 7},
        }
        with patch.dict(os.environ, self.environment("image.generate"), clear=True), patch.object(
            media,
            "urlopen",
            return_value=FakeResponse(json.dumps(response).encode("utf-8"), "application/json"),
        ) as mocked:
            result = media.generate_image("A blue robot", seed=7)

        self.assertEqual(png, result.data)
        self.assertEqual("image/png", result.content_type)
        self.assertEqual({"seed": 7}, result.metadata)
        request = mocked.call_args.args[0]
        self.assertEqual("Bearer cwmedia_ws_secret", request.get_header("Authorization"))
        payload = json.loads(request.data)
        self.assertEqual("A blue robot", payload["prompt"])
        self.assertEqual(10, payload["wait_seconds"])

    def test_synthesize_speech_requests_and_accepts_mp3(self):
        mp3 = bytes.fromhex("fff384c4") + bytes(380)
        with patch.dict(os.environ, self.environment("audio.speech"), clear=True), patch.object(
            media, "urlopen", return_value=FakeResponse(mp3, "audio/mpeg")
        ) as send:
            result = media.synthesize_speech("Hello class")
        self.assertEqual(result.content_type, "audio/mpeg")
        self.assertEqual(result.file_extension, ".mp3")
        self.assertEqual(json.loads(send.call_args.args[0].data)["response_format"], "mp3")

    def test_synthesize_speech_returns_wav(self):
        wav = b"RIFF\x04\x00\x00\x00WAVEdata"
        with patch.dict(os.environ, self.environment("audio.speech"), clear=True), patch.object(
            media,
            "urlopen",
            return_value=FakeResponse(wav, "audio/wav"),
        ):
            result = media.synthesize_speech("Hello class")
        self.assertEqual(wav, result.data)
        self.assertEqual("audio/wav", result.content_type)

    def test_new_model_options_are_forwarded_without_exposing_credentials(self):
        png = media.PNG_SIGNATURE + b"prototype"
        response = {
            "data": [{"b64_json": base64.b64encode(png).decode("ascii")}],
        }
        with patch.dict(
            os.environ,
            self.environment("image.generate"),
            clear=True,
        ), patch.object(
            media,
            "urlopen",
            return_value=FakeResponse(json.dumps(response).encode(), "application/json"),
        ) as mocked:
            media.generate_image("A cat", model="lcm-sd15", steps=4)
        payload = json.loads(mocked.call_args.args[0].data)
        self.assertEqual("lcm-sd15", payload["model"])
        self.assertEqual(4, payload["steps"])

    def test_inpainting_requires_a_mask(self):
        with patch.dict(os.environ, self.environment("image.edit"), clear=True):
            with self.assertRaisesRegex(media.CourseMediaError, "mask is required"):
                media.edit_image(
                    "replace the center",
                    b"source",
                    model="stable-diffusion-v1-5-inpainting",
                )

    def test_missing_or_ungranted_configuration_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(media.CourseMediaError, "GIZMO_MEDIA_OPERATIONS"):
                media.generate_image("hello")
        with patch.dict(os.environ, self.environment("audio.speech"), clear=True):
            with self.assertRaisesRegex(media.CourseMediaError, "not enabled"):
                media.generate_image("hello")

    def test_media_timeout_allows_bounded_model_startup(self):
        environment = self.environment("image.generate")
        environment.pop("GIZMO_MEDIA_TIMEOUT_SECONDS")
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(300.0, media._timeout_seconds())
        environment["GIZMO_MEDIA_TIMEOUT_SECONDS"] = "600"
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(600.0, media._timeout_seconds())
        environment["GIZMO_MEDIA_TIMEOUT_SECONDS"] = "601"
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(media.CourseMediaError, "at most 600"):
                media._timeout_seconds()

    def test_inputs_and_outputs_are_bounded(self):
        with patch.dict(os.environ, self.environment("image.generate,image.edit"), clear=True):
            with self.assertRaises(media.CourseMediaError):
                media.generate_image("hello", steps=31)
            with self.assertRaises(media.CourseMediaError):
                media.generate_image("x" * (media.MAX_PROMPT_CHARS + 1))
            with self.assertRaises(media.CourseMediaError):
                media.edit_image("hello", b"x" * (media.MAX_IMAGE_BYTES + 1))
            with self.assertRaises(media.CourseMediaError):
                media.generate_image("hello", model="sdxl")
            with self.assertRaises(media.CourseMediaError):
                media.synthesize_speech("hello", speed=3)

    def test_http_errors_do_not_leak_upstream_details(self):
        error = HTTPError(
            "https://course.example",
            502,
            "secret internal worker path",
            Message(),
            None,
        )
        with patch.dict(os.environ, self.environment("audio.speech"), clear=True), patch.object(
            media,
            "urlopen",
            side_effect=error,
        ):
            with self.assertRaisesRegex(media.CourseMediaError, "could not complete") as caught:
                media.synthesize_speech("Hello")
        self.assertNotIn("secret internal worker path", str(caught.exception))

    def test_safe_platform_error_is_preserved(self):
        headers = Message()
        headers["Content-Type"] = "application/json"
        error = HTTPError(
            "https://course.example",
            503,
            "Service Unavailable",
            headers,
            BytesIO(
                json.dumps(
                    {"error": "no live course-media worker currently supports this model; please try again shortly"}
                ).encode()
            ),
        )
        with patch.dict(os.environ, self.environment("audio.speech"), clear=True), patch.object(
            media,
            "urlopen",
            side_effect=error,
        ):
            with self.assertRaisesRegex(media.CourseMediaError, "no live course-media worker"):
                media.synthesize_speech("Hello")

    def test_busy_worker_returns_actionable_safe_error(self):
        queued = json.dumps({"id": "media-0123456789abcdef0123", "status": "queued"}).encode()
        with patch.dict(os.environ, self.environment("image.generate"), clear=True), patch.object(
            media,
            "urlopen",
            return_value=FakeResponse(queued, "application/json", status=202),
        ):
            with self.assertRaisesRegex(media.CourseMediaError, "worker is busy"):
                media.generate_image("Hello")


if __name__ == "__main__":
    unittest.main()
