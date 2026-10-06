"""A local ComfyUI server (https://github.com/Comfy-Org/ComfyUI), driven through its HTTP API.

ComfyUI runs the AI models (ACE-Step for music) with its own embedded Python and PyTorch, so
the bot's environment stays free of them. The bot starts the server only when it needs it and
stops it afterwards: the model takes RAM that the video render needs.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

START_TIMEOUT_S = 240


class ComfyError(RuntimeError):
    pass


class ComfyServer:
    """Context manager: reuses a running server, or starts one and stops it on exit."""

    def __init__(self, root: Path, url: str, low_vram: bool = False):
        self.root, self.url = Path(root), url.rstrip("/")
        self.low_vram = low_vram
        self.process: subprocess.Popen | None = None

    def alive(self) -> bool:
        try:
            with urllib.request.urlopen(self.url + "/system_stats", timeout=3):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def __enter__(self) -> ComfyServer:
        if self.alive():
            return self
        python = self.root / "python_embeded" / "python.exe"
        if not python.exists():
            raise ComfyError(f"ComfyUI not found at {self.root} (COMFYUI_DIR)")
        port = self.url.rsplit(":", 1)[-1]
        log.info("starting ComfyUI at %s", self.url)
        # ACE-Step (7 GB) fits the 8 GB laptop GPU: full speed, ~90 s per 3-minute track. --lowvram
        # (only part of a model on the GPU at a time) is for bigger models such as Wan 2.2; it makes
        # music ~50 % slower, so it is off unless asked for.
        self.process = subprocess.Popen(
            [str(python), "-s", "ComfyUI/main.py", "--windows-standalone-build", "--listen", "127.0.0.1",
             "--port", port, "--disable-auto-launch", *(["--lowvram"] if self.low_vram else [])],
            cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        started = time.time()
        while not self.alive():
            if self.process.poll() is not None:
                raise ComfyError(f"ComfyUI exited during start-up (code {self.process.returncode})")
            if time.time() - started > START_TIMEOUT_S:
                self.__exit__(None, None, None)
                raise ComfyError(f"ComfyUI did not start within {START_TIMEOUT_S} s")
            time.sleep(2)
        return self

    def __exit__(self, *exc) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
            log.info("stopped ComfyUI")

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(self.url + path, timeout=30) as response:
            return json.load(response)

    def run(self, graph: dict, timeout: float = 1800) -> list[Path]:
        """Queues an API-format graph, waits for it, and returns the files it saved."""
        body = json.dumps({"prompt": graph, "client_id": uuid.uuid4().hex}).encode()
        request = urllib.request.Request(self.url + "/prompt", data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                prompt_id = json.load(response)["prompt_id"]
        except urllib.error.HTTPError as exc:
            raise ComfyError(f"ComfyUI rejected the job: {exc.read().decode(errors='replace')[:2000]}") from exc
        started = time.time()
        while time.time() - started < timeout:
            entry = self._get(f"/history/{prompt_id}").get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyError(json.dumps(status.get("messages"))[-2000:])
                if status.get("completed"):
                    return [
                        self.root / "ComfyUI" / "output" / item.get("subfolder", "") / item["filename"]
                        for output in entry["outputs"].values()
                        for kind in ("audio", "images", "videos")
                        for item in output.get(kind, [])
                    ]
            time.sleep(3)
        raise ComfyError(f"ComfyUI job {prompt_id} did not finish within {timeout:.0f} s")
