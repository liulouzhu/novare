import asyncio
import os
import time
import io
import tarfile
import logging
import docker
from docker.errors import NotFound

logger = logging.getLogger(__name__)

IMAGE = "research-sandbox:latest"
IDLE_TIMEOUT = 1800  # 30 minutes


class DockerSandboxManager:
    """Manages per-user Docker sandboxes for code execution."""

    def __init__(self):
        try:
            self.client = docker.from_env()
        except Exception as e:
            logger.warning("Docker not available: %s", e)
            self.client = None
        self._containers: dict[str, docker.models.containers.Container] = {}
        self._last_used: dict[str, float] = {}

    def _container_name(self, user_id: str) -> str:
        return f"sandbox-{user_id[:12]}"

    def get_or_create(self, user_id: str):
        if not self.client:
            raise RuntimeError("Docker not available")

        name = self._container_name(user_id)

        # Reuse existing running container
        if user_id in self._containers:
            container = self._containers[user_id]
            try:
                container.reload()
                if container.status == "running":
                    self._last_used[user_id] = time.time()
                    return container
            except Exception:
                pass
            del self._containers[user_id]

        # Try to find existing container
        try:
            container = self.client.containers.get(name)
            if container.status != "running":
                container.start()
            self._containers[user_id] = container
            self._last_used[user_id] = time.time()
            return container
        except NotFound:
            pass

        # Create new container with security constraints
        # Use absolute path so Docker treats it as a bind mount, not a named volume
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        workspace = os.path.join(base_dir, "data", "workspaces", user_id)
        container = self.client.containers.run(
            IMAGE,
            detach=True,
            stdin_open=True,
            name=name,
            mem_limit="512m",
            memswap_limit="512m",
            nano_cpus=1_000_000_000,  # 1 CPU
            pids_limit=100,
            network_disabled=True,
            read_only=True,
            security_opt=["no-new-privileges"],
            cap_drop=["ALL"],
            user="1000:1000",
            tmpfs={"/tmp": "size=256m"},
            volumes={
                f"{workspace}/data": {"bind": "/data", "mode": "ro"},
                f"{workspace}/output": {"bind": "/output", "mode": "rw"},
            },
        )
        self._containers[user_id] = container
        self._last_used[user_id] = time.time()
        return container

    async def execute(self, user_id: str, code: str, timeout: int = 60) -> dict:
        """Execute code in user's sandbox container via executor.py (with full restrictions)."""
        if not code or not code.strip():
            return {"exit_code": 0, "stdout": "", "stderr": ""}
        if len(code.encode("utf-8")) > 50 * 1024:
            raise ValueError("Code too large (max 50KB)")

        container = self.get_or_create(user_id)

        # Write code to a temp file inside the container
        code_bytes = code.encode("utf-8")
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            info = tarfile.TarInfo(name="_run.py")
            info.size = len(code_bytes)
            tar.addfile(info, io.BytesIO(code_bytes))
        tar_stream.seek(0)
        await asyncio.to_thread(container.put_archive, "/tmp", tar_stream)

        # Execute through executor.py — enforces import/builtin/path/timeout/output limits
        effective_timeout = max(1, min(timeout, 300))
        try:
            exit_code, (stdout, stderr) = await asyncio.wait_for(
                asyncio.to_thread(
                    container.exec_run,
                    cmd=["python", "-u", "/executor.py", "/tmp/_run.py"],
                    demux=True,
                    environment={"TIMEOUT_SECONDS": str(effective_timeout)},
                ),
                timeout=effective_timeout + 5,
            )
        except asyncio.TimeoutError:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Error: Execution timed out ({effective_timeout}s)",
            }
        except Exception as e:
            logger.error("Sandbox execution error: %s", e)
            raise RuntimeError(f"Sandbox execution failed: {e}")

        self._last_used[user_id] = time.time()

        # Best-effort cleanup of temp file
        try:
            await asyncio.to_thread(
                container.exec_run,
                cmd=["rm", "-f", "/tmp/_run.py"],
                demux=True,
            )
        except Exception:
            pass

        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        if exit_code != 0 and err:
            raise RuntimeError(f"Code execution failed:\n{err}")
        return {"exit_code": exit_code, "stdout": out, "stderr": err}

    def cleanup_idle(self):
        now = time.time()
        for user_id in list(self._last_used.keys()):
            if now - self._last_used[user_id] > IDLE_TIMEOUT:
                self.destroy(user_id)

    def destroy(self, user_id: str):
        if user_id in self._containers:
            try:
                self._containers[user_id].remove(force=True)
            except Exception:
                pass
            del self._containers[user_id]
            self._last_used.pop(user_id, None)

    def shutdown(self):
        for user_id in list(self._containers.keys()):
            self.destroy(user_id)


# Global singleton
sandbox_manager = DockerSandboxManager()
