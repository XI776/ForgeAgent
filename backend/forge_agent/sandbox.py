from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LINUX_BWRAP = "linux-bubblewrap"
CONTAINER = "container"
HOST = "host"
UNAVAILABLE = "unavailable"
POLICIES = {"host", "workspace", "readonly"}
NETWORKS = {"allowed", "denied"}
BACKENDS = {"auto", "bubblewrap", "container"}
SYSTEM_ROOTS = ("/usr", "/etc", "/opt")
SYSTEM_LINKS = ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
PROBE_TIMEOUT = 5.0
_PROBE_CACHE: dict[tuple[str, str, bool], bool] = {}


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ProcessLaunch:
    command: list[str]
    cwd: Path
    env: dict[str, str]
    backend: str


@dataclass(frozen=True)
class SandboxCapabilities:
    backend: str
    workspace: bool
    readonly: bool
    network_denied: bool
    reason: str | None = None


def _inside_container() -> bool:
    return bool(os.getenv("container")) or Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


class SandboxSupervisor:
    SAFE_ENV = {"PATH", "LANG", "LC_ALL", "TERM", "HOME"}

    def __init__(self, workspace: Path, *, home: Path | None = None, backend: str | None = None):
        self.workspace = Path(workspace).resolve()
        self.home = Path(home).resolve() if home else None
        preference = (backend if backend is not None else os.getenv("FORGEAGENT_SANDBOX_BACKEND", "auto")).strip().lower()
        self.preference = preference if preference in BACKENDS else "auto"

    def resolve(self, raw: str, *, must_exist: bool = False) -> Path:
        candidate = (self.workspace / raw).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise SandboxError("Path escapes the workspace") from exc
        if must_exist and not candidate.exists():
            raise SandboxError(f"Path does not exist: {raw}")
        return candidate

    def clean_environment(self, additions: dict[str, Any] | None = None) -> dict[str, str]:
        # Child processes receive only launch essentials plus explicit config,
        # avoiding accidental inheritance of host credentials.
        env = {key: value for key, value in os.environ.items() if key.upper() in self.SAFE_ENV}
        for key, value in (additions or {}).items():
            name = str(key)
            if not name or "=" in name or "\x00" in name:
                raise SandboxError(f"Invalid environment variable name: {name!r}")
            env[name] = str(value)
        return env

    def prepare_launch(
        self,
        executable: str,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, Any] | None = None,
        filesystem: str = "workspace",
        network: str = "allowed",
        readable_roots: list[str] | None = None,
        writable_roots: list[str] | None = None,
        allowed_domains: list[str] | None = None,
    ) -> ProcessLaunch:
        """Validate policy before any child process is created, for Shell and MCP alike."""
        if filesystem not in POLICIES or network not in NETWORKS:
            raise SandboxError("Unsupported sandbox policy; process was not started")
        if readable_roots or writable_roots or allowed_domains:
            raise SandboxError("Custom sandbox roots and domain allowlists are unavailable; process was not started")
        working = (cwd or self.workspace).resolve()
        try:
            working.relative_to(self.workspace)
        except ValueError as exc:
            raise SandboxError("Process cwd escapes the workspace") from exc
        command = [executable, *args]
        environment = self.clean_environment(env)
        if filesystem == "host" and network == "allowed":
            return ProcessLaunch(command, working, environment, HOST)

        backend = self._select_backend(filesystem=filesystem, network=network)
        if backend == LINUX_BWRAP:
            if self.home:
                self.home.mkdir(parents=True, exist_ok=True)
                environment["HOME"] = str(self.home)
            bwrap = self._bwrap_executable()
            if not bwrap:
                raise SandboxError("bubblewrap is unavailable; process was not started")
            return ProcessLaunch(
                self._bubblewrap_arguments(
                    bwrap,
                    filesystem=filesystem,
                    network=network,
                    working=working,
                    command=command,
                ),
                working,
                environment,
                LINUX_BWRAP,
            )
        if backend == CONTAINER:
            if self.home:
                self.home.mkdir(parents=True, exist_ok=True)
                environment["HOME"] = str(self.home)
            return ProcessLaunch(command, working, environment, CONTAINER)
        raise SandboxError("Workspace sandbox is unavailable; process was not started")

    def supports(self, *, filesystem: str, network: str) -> bool:
        if filesystem not in POLICIES or network not in NETWORKS:
            return False
        if filesystem == "host" and network == "allowed":
            return True
        capabilities = self.capabilities()
        if filesystem == "workspace" and not capabilities.workspace:
            return False
        if filesystem == "readonly" and not capabilities.readonly:
            return False
        if network == "denied" and not capabilities.network_denied:
            return False
        return capabilities.backend != UNAVAILABLE

    def capabilities(self) -> SandboxCapabilities:
        if not sys.platform.startswith("linux"):
            return SandboxCapabilities(UNAVAILABLE, False, False, False, "Workspace sandbox is only available on Linux")
        if self.preference == "container":
            return self._container_capabilities()
        if self.preference in {"auto", "bubblewrap"}:
            if self._bubblewrap_available(network_denied=True):
                return SandboxCapabilities(LINUX_BWRAP, True, True, True)
            filesystem_available = self._bubblewrap_available(network_denied=False)
            if filesystem_available:
                return SandboxCapabilities(LINUX_BWRAP, True, True, False, "bubblewrap cannot create a network namespace")
            if self.preference == "bubblewrap":
                return SandboxCapabilities(UNAVAILABLE, False, False, False, self._bubblewrap_error())
        if self.preference == "auto" and _inside_container():
            return self._container_capabilities()
        return SandboxCapabilities(
            UNAVAILABLE,
            False,
            False,
            False,
            "bubblewrap is not installed and this process is not running inside a container",
        )

    @staticmethod
    def _container_capabilities() -> SandboxCapabilities:
        return SandboxCapabilities(
            CONTAINER,
            True,
            False,
            False,
            "Container isolation only supports filesystem=workspace with network=allowed",
        )

    def _select_backend(self, *, filesystem: str, network: str) -> str:
        if not sys.platform.startswith("linux"):
            raise SandboxError("Workspace sandbox is only available on Linux; process was not started")
        if self.preference == "container":
            self._require_container_policy(filesystem=filesystem, network=network)
            return CONTAINER
        if self.preference in {"auto", "bubblewrap"}:
            if self._bubblewrap_available(network_denied=(network == "denied")):
                return LINUX_BWRAP
            if self.preference == "bubblewrap":
                raise SandboxError(f"{self._bubblewrap_error()}; process was not started")
        if self.preference == "auto" and _inside_container():
            self._require_container_policy(filesystem=filesystem, network=network)
            return CONTAINER
        reason = "bubblewrap is not installed" if not self._bwrap_executable() else self._bubblewrap_error()
        raise SandboxError(
            f"Workspace sandbox is unavailable ({reason}); install bubblewrap or run inside the Docker container"
        )

    @staticmethod
    def _require_container_policy(*, filesystem: str, network: str) -> None:
        if filesystem != "workspace" or network != "allowed":
            raise SandboxError(
                "Container sandbox can only enforce filesystem=workspace with network=allowed; "
                "process was not started"
            )

    @staticmethod
    def _bwrap_executable() -> str | None:
        return shutil.which("bwrap")

    def _bubblewrap_error(self) -> str:
        if not self._bwrap_executable():
            return "bubblewrap is not installed"
        return "bubblewrap cannot create the required namespaces or mounts"

    def _bubblewrap_available(self, *, network_denied: bool) -> bool:
        bwrap = self._bwrap_executable()
        if not bwrap:
            return False
        key = (bwrap, f"{self.workspace}\0{self.home}", network_denied)
        cached = _PROBE_CACHE.get(key)
        if cached is not None:
            return cached
        if self.home:
            self.home.mkdir(parents=True, exist_ok=True)
        command = self._bubblewrap_arguments(
            bwrap,
            filesystem="readonly",
            network="denied" if network_denied else "allowed",
            working=self.workspace,
            command=["/bin/sh", "-c", "true"],
        )
        try:
            result = subprocess.run(
                command,
                cwd=self.workspace,
                env=self.clean_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=PROBE_TIMEOUT,
                check=False,
            )
            available = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            available = False
        _PROBE_CACHE[key] = available
        return available

    def _bubblewrap_arguments(
        self,
        bwrap: str,
        *,
        filesystem: str,
        network: str,
        working: Path,
        command: list[str],
    ) -> list[str]:
        args = [bwrap, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts"]
        if filesystem == "host":
            # Host mode deliberately exposes the whole filesystem; only the
            # network namespace (when requested) provides isolation.
            args.extend(["--bind", "/", "/", "--proc", "/proc", "--dev", "/dev"])
            if network == "denied":
                args.append("--unshare-net")
            args.extend(["--chdir", str(working), "--", *command])
            return args

        created: set[str] = {"/proc", "/dev", "/dev/shm", "/tmp", "/run", "/var", "/var/tmp"}
        args.extend(
            [
                "--dir",
                "/proc",
                "--proc",
                "/proc",
                "--dir",
                "/dev",
                "--dev",
                "/dev",
                "--dir",
                "/dev/shm",
                "--tmpfs",
                "/dev/shm",
                "--dir",
                "/tmp",
                "--tmpfs",
                "/tmp",
                "--dir",
                "/run",
                "--tmpfs",
                "/run",
                "--dir",
                "/var",
                "--dir",
                "/var/tmp",
                "--tmpfs",
                "/var/tmp",
            ]
        )
        for root in SYSTEM_ROOTS:
            if Path(root).exists():
                args.extend(["--ro-bind", root, root])
        for link in SYSTEM_LINKS:
            self._append_system_path(args, link)
        self._append_resolv_conf(args, created, network=network)
        mounts = [(self.workspace, filesystem == "readonly")]
        if self.home:
            mounts.append((self.home, False))
            if self.workspace.is_relative_to(self.home):
                # A later bind of the parent would hide the workspace mount.
                mounts.reverse()
        for source, readonly in mounts:
            self._append_mount(args, created, source, readonly=readonly)
        if network == "denied":
            args.append("--unshare-net")
        args.extend(["--chdir", str(working), "--", *command])
        return args

    @staticmethod
    def _append_system_path(args: list[str], path: str) -> None:
        candidate = Path(path)
        if candidate.is_symlink():
            args.extend(["--symlink", os.readlink(candidate), path])
        elif candidate.exists():
            args.extend(["--ro-bind", path, path])

    @staticmethod
    def _append_mount(args: list[str], created: set[str], source: Path, *, readonly: bool) -> None:
        for parent in reversed(source.parents):
            if parent != Path("/"):
                SandboxSupervisor._append_dir(args, created, str(parent))
        SandboxSupervisor._append_dir(args, created, str(source))
        args.extend(["--ro-bind" if readonly else "--bind", str(source), str(source)])

    @staticmethod
    def _append_dir(args: list[str], created: set[str], path: str) -> None:
        if path not in created:
            args.extend(["--dir", path])
            created.add(path)

    def _append_resolv_conf(self, args: list[str], created: set[str], *, network: str) -> None:
        original = Path("/etc/resolv.conf")
        if not original.exists():
            return
        if original.is_symlink():
            if network != "allowed":
                return
            target_parent = original.resolve().parent
            if any(str(target_parent).startswith(root) for root in ("/etc", "/usr", "/opt")):
                return
            # Only expose the small resolver directories used by systemd-resolved
            # and WSL instead of binding an arbitrary symlink target.
            if not any(str(target_parent).startswith(root) for root in ("/run", "/var", "/mnt")):
                return
            for parent in reversed(target_parent.parents):
                if parent != Path("/"):
                    self._append_dir(args, created, str(parent))
            self._append_dir(args, created, str(target_parent))
            args.extend(["--ro-bind", str(target_parent), str(target_parent)])
            return
        if not self.home:
            return
        try:
            content = original.resolve().read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        copy = self.home / "resolv.conf"
        try:
            copy.write_text(content, encoding="utf-8")
        except OSError:
            return
        args.extend(["--ro-bind", str(copy), "/etc/resolv.conf"])

    async def run(
        self,
        executable: str,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, Any] | None = None,
        timeout: float = 120,
        filesystem: str = "workspace",
        network: str = "allowed",
    ) -> ProcessResult:
        launch = self.prepare_launch(
            executable, args, cwd=cwd, env=env, filesystem=filesystem, network=network,
        )

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *launch.command,
                cwd=launch.cwd,
                env=launch.env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError as exc:
            if process and process.returncode is None:
                self._kill_process_tree(process.pid)
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
            raise SandboxError(f"Process timed out after {timeout:g}s") from exc
        return ProcessResult(
            process.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    @staticmethod
    def _kill_process_tree(pid: int) -> None:
        if not sys.platform.startswith("linux"):
            return
        pending, seen = [pid], set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            try:
                children = Path(f"/proc/{current}/task/{current}/children").read_text(encoding="ascii").split()
            except OSError:
                children = []
            pending.extend(int(child) for child in children if child.isdigit())
        for target in seen:
            try:
                os.kill(target, signal.SIGKILL)
            except OSError:
                pass


def sandbox_capabilities(workspace: Path, *, home: Path | None = None, backend: str | None = None) -> SandboxCapabilities:
    return SandboxSupervisor(workspace, home=home, backend=backend).capabilities()
