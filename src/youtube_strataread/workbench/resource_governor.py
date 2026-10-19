"""One admission decision for managed browser maintenance and new acquisition.

Observations are replaceable at the kernel boundary. Permissions stay in the
workspace: recovery merely makes otherwise runnable work eligible for a slot.
"""
import os
import time
from pathlib import Path
from dataclasses import dataclass

from youtube_strataread.workbench.content_session import ContentSessionError

MIB = 1024 * 1024
BROWSER_LIMIT = 768 * MIB
START_AVAILABLE = 1280 * MIB
RUN_AVAILABLE = 512 * MIB
# Release before the 576 MiB kernel high watermark stalls browser allocation.
BROWSER_RELEASE = 544 * MIB


@dataclass(frozen=True)
class ResourceObservation:
    available: int
    browser_memory: int
    browser_swap: int
    constrained: bool


class ResourceGovernor:
    def __init__(self, *, observe=None, now=time.monotonic):
        self.now = now
        self._browser_retry_at = 0.0
        self._browser_releases = 0
        self.observe = observe or LinuxResources.from_environment().observe

    def status(self):
        sample = self.observe()
        reason = None
        if not sample.constrained:
            reason = "服务器浏览器资源隔离不可用，请检查内存与 swap 上限。"
        elif sample.browser_memory >= BROWSER_RELEASE or sample.browser_swap:
            reason = "服务器浏览器接近内存预算，正在释放维护资源。"
        elif sample.available < RUN_AVAILABLE:
            reason = "服务器可用内存不足，等待资源恢复；已有阅读和在途成稿继续。"
        browser_backoff = self.now() < self._browser_retry_at
        return {
            "browser_waiting": bool(reason) or sample.available < START_AVAILABLE or browser_backoff,
            "captions_waiting": bool(reason),
            "release_browser": bool(reason),
            "reason": reason or ("服务器可用内存不足 1.25 GiB，等待打开登录画面。" if sample.available < START_AVAILABLE else "维护浏览器已因资源压力关闭，稍后再检查资源。" if browser_backoff else None),
            "available_bytes": sample.available,
            "browser_memory_bytes": sample.browser_memory,
            "browser_swap_bytes": sample.browser_swap,
        }

    def browser_released(self):
        # Avoid repeatedly relaunching a page which just exceeded its budget.
        self._browser_releases = min(self._browser_releases + 1, 4)
        self._browser_retry_at = self.now() + min(60 * 2 ** (self._browser_releases - 1), 300)

    def require_browser_start(self):
        state = self.status()
        if state["browser_waiting"]:
            raise ContentSessionError("resource", state["reason"])

    def require_captions(self):
        state = self.status()
        if state["captions_waiting"]:
            raise ContentSessionError("resource", state["reason"])


class LinuxResources:
    """Read kernel-enforced limits, never trust a configured numeric budget alone."""
    def __init__(self, group, *, proc=Path("/proc"), cgroup_root=Path("/sys/fs/cgroup")):
        self.group = Path(group).resolve() if group else None
        self.proc = proc
        self.cgroup_root = cgroup_root.resolve()

    @classmethod
    def from_environment(cls):
        return cls(os.environ.get("EDISON_CONTENT_CGROUP_DIR"))

    def _limits(self):
        group = self.group
        if group is None or group == self.cgroup_root or not group.is_relative_to(self.cgroup_root):
            raise ValueError("dedicated cgroup required")
        limit = int((group / "memory.max").read_text())
        high = int((group / "memory.high").read_text())
        swap_limit = int((group / "memory.swap.max").read_text())
        if high != 576 * MIB or limit != BROWSER_LIMIT or swap_limit != 0:
            raise ValueError("unsafe cgroup budget")
        return group

    def observe(self, *, browser_process=False):
        available = memory = swap = 0
        try:
            fields = dict(line.split(":", 1) for line in (self.proc / "meminfo").read_text().splitlines())
            available = int(fields["MemAvailable"].split()[0]) * 1024
            group = self._limits()
            own = next(line[3:] for line in (self.proc / "self/cgroup").read_text().splitlines() if line.startswith("0::"))
            own_group = self.cgroup_root / own.lstrip("/")
            if not browser_process and (own_group == group or own_group.is_relative_to(group)):
                raise ValueError("host must stay outside browser group")
            memory = int((group / "memory.current").read_text())
            swap = int((group / "memory.swap.current").read_text())
            return ResourceObservation(available, memory, swap, True)
        except (OSError, ValueError, KeyError, StopIteration):
            return ResourceObservation(available, memory, swap, False)

    def enter_browser_group(self):
        """Called before Xvfb/Chrome; children inherit the same hard budget."""
        group = self._limits()
        own = next(line[3:] for line in (self.proc / "self/cgroup").read_text().splitlines() if line.startswith("0::"))
        current = self.cgroup_root / own.lstrip("/")
        if current != group:
            # Write in the child, before any external program starts. Never move
            # the host or a parent process to make an invalid deployment work.
            (group / "cgroup.procs").write_text(str(os.getpid()))
        own = next(line[3:] for line in (self.proc / "self/cgroup").read_text().splitlines() if line.startswith("0::"))
        if self.cgroup_root / own.lstrip("/") != group:
            raise ValueError("browser cgroup membership unconfirmed")
