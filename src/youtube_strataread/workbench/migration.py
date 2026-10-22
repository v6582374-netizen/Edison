"""Idempotent historical migration inside a YouTube cutover hold.

The canonical library path, credential namespace and OAuth client stay unless
the maintainer passes an explicit map. Account names are never discovered by
scanning the credential file. Stored fields do not make an OAuth grant valid.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
from contextlib import nullcontext
from pathlib import Path

from youtube_strataread.downloader.request_policy import YouTubeRequestPolicy
from youtube_strataread.workbench.cutover import CutoverBlocked
from youtube_strataread.workbench.keychain import NativeKeychainVault, ServiceAccountFile

_KEY = "youtube_historical_migration"
_PATH_MARKER = "youtube_asset_path_migration"
_CLIENT = "YOUTUBE_WORKBENCH_OAUTH_CLIENT_ID"
_SECRET = "YOUTUBE_WORKBENCH_OAUTH_CLIENT_SECRET"
_REFRESH = "YOUTUBE_WORKBENCH_OAUTH_REFRESH_TOKEN"
_RATE_DELAY = 15 * 60
_NETWORK_DELAY = 10 * 60
_429 = re.compile(r"HTTP(?: Error)?[ :]+429\b|Too Many Requests", re.I)
_SUBTITLE = re.compile(r"download video subtitles|subtitle|caption|字幕获取", re.I)
_NETWORK = re.compile(
    r"timed? ?out|connection (?:reset|refused|aborted)|temporary failure|network is unreachable|"
    r"remote end closed|name resolution|HTTP Error 5\d\d|EOF occurred|ConnectError|ReadTimeout|"
    r"URLError|APIConnectionError|APITimeoutError", re.I)
_STAGES = ("credentials", "paths", "failures")


def _label(record: dict) -> str:
    if record.get("permission_error"):
        return "凭据存储权限不足，未把字段存在当作授权有效。"
    if record.get("client_match") is False:
        return "OAuth client 不匹配，已保留原 client。"
    if record.get("identity_withdrawn"):
        return "身份已断开，迁移不会重新连接。"
    if record.get("finished"):
        return "历史迁移已完成"
    if record.get("stage") in _STAGES:
        return {"credentials": "正在核对凭据存储", "paths": "正在映射资料路径", "failures": "正在迁移历史失败"}[record["stage"]]
    return "尚未迁移"


class HistoricalMigration:
    def __init__(self, workspace, vault, *, identity_lock=None) -> None:
        self.workspace = workspace
        self.vault = vault
        self.identity_lock = identity_lock

    def _lock(self):
        return self.identity_lock if self.identity_lock is not None else nullcontext()

    def _withdrawn(self) -> bool:
        return self.workspace.meta("youtube_disconnect_local") in {"pending", "failed", "cleared"}

    def _record(self) -> dict:
        raw = self.workspace.meta(_KEY)
        if not raw:
            return {}
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}

    def _save(self, record: dict) -> None:
        # The stage log is maintainer-visible. It must not carry credential material.
        self.workspace.set_meta(_KEY, json.dumps(record, ensure_ascii=False))

    def status(self) -> dict:
        record = {
            "stage": "not_started", "finished": False, "runs": 0, "completed": [],
            "canonical_namespace_kept": True, "canonical_paths_kept": True,
            "client_match": None, "permission_error": False, "identity_withdrawn": False,
            "source_missing": False, "rotation_kept": None,
            "credential_check": {"read": False, "atomic_replace": False, "owner_only": False},
            "outside_library": [], "shared_wait": {"rate_limited": 0, "network": 0},
            **self._record(),
        }
        record["identity_withdrawn"] = bool(record.get("identity_withdrawn") or self._withdrawn())
        record["oauth_authorized"] = self.workspace.meta("youtube_oauth_authorized") == "1"
        record["unknown"] = self._live_unknown() if "failures" in record.get("completed", []) else []
        record["stage_label"] = _label(record)
        return record

    def run(self, plan: dict | None = None) -> dict:
        namespace, expected_client, path_map = self._plan(plan)
        held = json.loads(self.workspace.meta("youtube_cutover") or "{}")
        if not held.get("held"):
            raise CutoverBlocked("历史迁移只能在切换暂停内执行。")
        previous = self._record()
        record = {
            **previous, "stage": "credentials", "finished": False,
            "runs": int(previous.get("runs") or 0) + 1, "completed": [],
            "canonical_namespace_kept": namespace is None, "canonical_paths_kept": not path_map,
            "permission_error": False, "source_missing": False, "client_match": None,
            "identity_withdrawn": self._withdrawn(), "rotation_kept": None,
            "outside_library": [], "shared_wait": {"rate_limited": 0, "network": 0},
        }
        self._save(record)
        self._credentials(record, namespace, expected_client)
        record["completed"] = [*record["completed"], "credentials"]
        record["stage"] = "paths"
        self._save(record)
        record["outside_library"] = self._map_paths(path_map)
        record["completed"] = [*record["completed"], "paths"]
        record["stage"] = "failures"
        self._save(record)
        record["shared_wait"] = self._failures()
        record["completed"] = [*record["completed"], "failures"]
        record["stage"] = "complete"
        record["finished"] = True
        self._save(record)
        return self.status()

    def _plan(self, plan: dict | None) -> tuple:
        if plan is None:
            plan = {}
        if not isinstance(plan, dict) or set(plan) - {"credential_namespace", "expected_client_id", "path_map"}:
            raise ValueError("迁移计划必须是显式映射。")
        namespace = plan.get("credential_namespace")
        if namespace is not None and (not isinstance(namespace, str) or not namespace.strip()):
            raise ValueError("凭据命名空间必须显式给出。")
        expected = plan.get("expected_client_id")
        if namespace and not isinstance(expected, str):
            raise ValueError("搬移凭据必须显式给出要匹配的 OAuth client。")
        path_map = plan.get("path_map") or []
        if not isinstance(path_map, list):
            raise ValueError("路径映射必须是显式列表。")
        normalized = []
        for item in path_map:
            if not isinstance(item, dict) or not isinstance(item.get("from"), str) or not isinstance(item.get("to"), str):
                raise ValueError("路径映射的每一项都要给出 from 和 to。")
            if not Path(item["from"]).is_absolute() or not Path(item["to"]).is_absolute():
                raise ValueError("路径映射必须使用绝对路径。")
            normalized.append((item["from"], item["to"]))
        return namespace, expected, normalized

    def _backend(self):
        return self.vault._store()

    def _durable(self, vault) -> dict[str, str] | None:
        """None means the service account could not read the file."""
        backend = vault._store()
        try:
            raw = backend.get_password(vault.service, vault.account)
        except PermissionError:
            return None
        if not raw:
            return {}
        values = json.loads(raw)
        if not isinstance(values, dict):
            raise ValueError("invalid credential record")
        return {key: value for key, value in values.items() if isinstance(key, str) and isinstance(value, str)}

    def _credentials(self, record: dict, namespace: str | None, expected_client: str | None) -> None:
        with self._lock():
            record["identity_withdrawn"] = self._withdrawn()
            current = self._durable(self.vault)
            if current is None:
                record["permission_error"] = True
                record["credential_check"] = {"read": False, "atomic_replace": False, "owner_only": False}
                record["client_match"] = None
                return
            if record["identity_withdrawn"]:
                # A withdrawal that landed before this write owns the file.
                record["rotation_kept"] = "withdrawn"
                record["credential_check"] = {"read": True, "atomic_replace": False, "owner_only": self._owner_only()}
                record["client_match"] = True if namespace is None else None
                return
            backend = self._backend()
            if isinstance(backend, ServiceAccountFile):
                backend.set_password(self.vault.service, self.vault.account, json.dumps(current))
            record["credential_check"] = {
                "read": True,
                "atomic_replace": isinstance(backend, ServiceAccountFile) and self._replaced(backend),
                "owner_only": self._owner_only(),
            }
            if not isinstance(backend, ServiceAccountFile):
                record["credential_check"]["atomic_replace"] = True
            if namespace is None:
                record["canonical_namespace_kept"] = True
                record["client_match"] = True
                return
            source_vault = NativeKeychainVault(namespace=namespace, backend=backend)
            source = self._durable(source_vault)
            if source is None:
                record["permission_error"] = True
                record["credential_check"]["read"] = False
                return
            source_client = source.get(_CLIENT) or ""
            if not source_client:
                # One explicit namespace lookup. Other accounts in the file stay untouched.
                record["source_missing"] = True
                record["client_match"] = None
                return
            destination_client = current.get(_CLIENT) or ""
            if source_client != expected_client or (destination_client and destination_client != expected_client):
                record["client_match"] = False
                return
            record["client_match"] = True
            if self._withdrawn():
                record["identity_withdrawn"] = True
                record["rotation_kept"] = "withdrawn"
                return
            latest = self._durable(self.vault) or {}
            if latest.get(_REFRESH):
                record["rotation_kept"] = "canonical"
                return
            if not source.get(_SECRET) or not source.get(_REFRESH):
                record["rotation_kept"] = None
                return
            # Access tokens are not a grant. The canonical file keeps the refresh
            # token only, and authorization stays whatever the connection meta says.
            self.vault.save_many({_CLIENT: source_client, _SECRET: source[_SECRET], _REFRESH: source[_REFRESH]})
            record["rotation_kept"] = "source"

    def _owner_only(self) -> bool:
        backend = self._backend()
        if not isinstance(backend, ServiceAccountFile) or not backend.path.exists():
            return False
        info = backend.path.stat()
        return stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077

    def _replaced(self, backend: ServiceAccountFile) -> bool:
        return not list(backend.path.parent.glob(".edison-credentials-*")) and self._owner_only()

    def _map_paths(self, path_map: list[tuple[str, str]]) -> list[str]:
        outside = []
        with sqlite3.connect(self.workspace.database_path) as connection:
            stored = [row[0] for row in connection.execute(
                """SELECT path FROM transcripts WHERE path IS NOT NULL
                   UNION SELECT path FROM manuscripts WHERE path IS NOT NULL
                   UNION SELECT manuscript_path FROM candidates WHERE manuscript_path IS NOT NULL"""
            ) if row[0]]
        seen = set()
        for stored_path in stored:
            if stored_path in seen:
                continue
            seen.add(stored_path)
            mapped = self._mapped(stored_path, path_map)
            if mapped is None:
                if not self._under_library(stored_path):
                    outside.append(stored_path)
                continue
            if Path(stored_path).is_symlink():
                outside.append(stored_path)
                continue
            self._relocate(stored_path, mapped)
            with sqlite3.connect(self.workspace.database_path) as connection:
                connection.execute("UPDATE transcripts SET path=? WHERE path=?", (mapped, stored_path))
                connection.execute("UPDATE manuscripts SET path=? WHERE path=?", (mapped, stored_path))
                connection.execute("UPDATE candidates SET manuscript_path=? WHERE manuscript_path=?", (mapped, stored_path))
        if path_map:
            self.workspace.set_meta(_PATH_MARKER, json.dumps(
                {"map": [{"from": source, "to": target} for source, target in path_map], "applied": True},
                ensure_ascii=False))
        return outside

    def _under_library(self, stored_path: str) -> bool:
        root = str(self.workspace.root.resolve())
        return stored_path == root or stored_path.startswith(root + os.sep)

    def _mapped(self, stored_path: str, path_map: list[tuple[str, str]]) -> str | None:
        matches = []
        for source, target in path_map:
            prefix = source.rstrip("/")
            if stored_path == prefix or stored_path.startswith(prefix + "/"):
                matches.append((prefix, target))
        if not matches:
            return None
        prefix, target = max(matches, key=lambda item: len(item[0]))
        suffix = stored_path[len(prefix):].lstrip("/")
        return str(Path(target) / suffix) if suffix else str(Path(target))

    def _relocate(self, stored_path: str, mapped: str) -> None:
        source, target = Path(stored_path), Path(mapped)
        if not source.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return
        os.replace(source, target)

    def _failures(self) -> dict[str, int]:
        counts = {"rate_limited": 0, "network": 0}
        deadlines = {"rate_limited": None, "network": None}
        evidence = {"rate_limited": [], "network": []}
        with sqlite3.connect(self.workspace.database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT video_id, preparation_state, failure_reason, acquisition_failure_kind,
                          preparation_completed_at, stage_updated_at, retry_at,
                          caption_first_absent_at, caption_checks_ended_at
                   FROM candidates
                   WHERE preparation_state IN ('unavailable', 'failed', 'rate_limited', 'waiting_retry')"""
            ).fetchall()
            for row in rows:
                kind = _classify(row)
                if kind == "unknown":
                    connection.execute(
                        """UPDATE candidates SET acquisition_failure_kind='pending_review'
                           WHERE video_id=? AND preparation_state IN ('unavailable', 'failed')
                             AND COALESCE(acquisition_failure_kind, '') IN ('', 'unknown', 'pending_review')
                             AND caption_checks_ended_at IS NULL""",
                        (row["video_id"],),
                    )
                    continue
                if kind not in {"rate_limited", "network"}:
                    continue
                when = _evidence_at(row)
                if when is not None:
                    evidence[kind].append(when)
                    delay = _RATE_DELAY if kind == "rate_limited" else _NETWORK_DELAY
                    deadline = when + delay
                    deadlines[kind] = deadline if deadlines[kind] is None else max(deadlines[kind], deadline)
                cursor = connection.execute(
                    """UPDATE candidates SET preparation_state='rate_limited', preparation_stage='rate_limited',
                              acquisition_failure_kind=?
                       WHERE video_id=? AND preparation_state IN ('unavailable', 'failed', 'rate_limited', 'waiting_retry')""",
                    (kind, row["video_id"]),
                )
                counts[kind] += cursor.rowcount
            self._merge_policy(connection, deadlines, evidence, counts)
            connection.commit()
        return counts

    def _merge_policy(self, connection, deadlines, evidence, counts) -> None:
        if not any(counts.values()):
            return
        state = YouTubeRequestPolicy._read(connection)
        winner = None
        for kind in ("rate_limited", "network"):
            deadline = deadlines[kind]
            if deadline is None:
                continue
            if winner is None or deadline >= winner[1]:
                winner = (kind, deadline)
        assert winner is not None
        state["cooldown_until"] = max(state.get("cooldown_until") or 0, winner[1])
        state["failure_kind"] = winner[0] if state["cooldown_until"] == winner[1] else state.get("failure_kind", winner[0])
        if counts["rate_limited"]:
            state["strikes"] = max(int(state.get("strikes") or 0), 1)
        if counts["network"]:
            state["network_failures"] = max(int(state.get("network_failures") or 0), 1)
        witnessed = [item for values in evidence.values() for item in values]
        if witnessed:
            state["first_failure_at"] = min([state["first_failure_at"], *witnessed]) if "first_failure_at" in state else min(witnessed)
            state["last_failure_at"] = max([state["last_failure_at"], *witnessed]) if "last_failure_at" in state else max(witnessed)
        else:
            state["failure_time_unverified"] = True
        if counts["rate_limited"] and counts["network"]:
            state["reason"] = "历史字幕限流与网络故障已迁入共享等待。"
        elif counts["rate_limited"]:
            state["reason"] = "历史字幕请求限流已迁入共享等待。"
        else:
            state["reason"] = "历史字幕网络故障已迁入共享等待。"
        YouTubeRequestPolicy._write(connection, state)

    def _live_unknown(self) -> list[dict[str, str]]:
        with sqlite3.connect(self.workspace.database_path) as connection:
            rows = connection.execute(
                """SELECT video_id, title FROM candidates
                   WHERE preparation_state IN ('failed', 'unavailable')
                     AND acquisition_failure_kind IN ('pending_review', 'unknown')
                     AND caption_checks_ended_at IS NULL
                   ORDER BY discovered_at, video_id"""
            ).fetchall()
        return [{"video_id": row[0], "title": row[1]} for row in rows]


def _evidence_at(row) -> float | None:
    for key in ("preparation_completed_at", "stage_updated_at"):
        value = row[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
            return float(value)
    return None


def _classify(row) -> str:
    if row["caption_first_absent_at"] is not None or row["caption_checks_ended_at"] is not None:
        return "leave"
    reason = row["failure_reason"] or ""
    kind = row["acquisition_failure_kind"] or ""
    if reason.startswith("已自动尝试") or "模型" in reason or "官方 API" in reason:
        return "leave"
    if kind in {"private", "deleted", "access_denied", "caption_pending"}:
        return "leave"
    subtitle = bool(_SUBTITLE.search(reason)) or kind in {"rate_limited", "network", "transient"}
    if kind == "rate_limited" or (_429.search(reason) and subtitle):
        return "rate_limited"
    if kind == "network" or (kind == "transient" and "字幕获取暂时失败" in reason) or (_NETWORK.search(reason) and subtitle):
        return "network"
    if row["preparation_state"] in {"unavailable", "failed"} and kind in {"", "unknown", "pending_review"}:
        return "unknown"
    return "leave"
