"""Sidecar-owned local workspace for the personal reading workbench."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from platformdirs import user_data_dir

from youtube_strataread.downloader.request_policy import (
    YouTubeRequestPolicy,
)
from youtube_strataread.workbench import acceptance
from youtube_strataread.workbench.freshness import WAITING_REASONS, preparation_admission
from youtube_strataread.workbench.retry import (
    MAX_AUTOMATIC_ATTEMPTS,
    Failure,
    ModelAccountGate,
    retry_delay,
)
from youtube_strataread.workbench.shorts import CLASSIFICATION_RETRY_SECONDS

# The library format this program can safely interpret. Bump it when an earlier
# program could misread the library or act against newer user intent; additive
# columns an earlier program simply ignores do not need a bump. The library
# records the highest contract that ever opened it and never lowers it.
YOUTUBE_DATA_CONTRACT = 2

# Stages in which a task holds no processing position while it waits.
YIELDING_STAGES = frozenset({"queued", "restored", "rate_limited", "waiting_retry", "awaiting_classification"})
# Stages that end or park a task outside preparation; their time is not processing.
PARKED_STAGES = frozenset({"ready", "failed", "unavailable", "cancelled", "filtered", "expired",
                           "awaiting_timing", "awaiting_completion"})

if TYPE_CHECKING:
    from youtube_strataread.workbench.connection import SubscriptionSource
    from youtube_strataread.workbench.discovery import Candidate


@dataclass(frozen=True)
class LibrarySnapshot:
    """The minimum library state available before subscription import exists."""

    label: str = "Local Library"
    status: str = "ready"
    inbox: int = 0
    to_read: int = 0
    reading: int = 0
    read: int = 0
    candidates: list[dict[str, object]] | None = None

    def as_result(self) -> dict[str, object]:
        return {
            "workspace": {"label": self.label, "status": self.status},
            "counts": {
                "inbox": self.inbox,
                "to_read": self.to_read,
                "reading": self.reading,
                "read": self.read,
            },
            "inbox": self.candidates or [],
        }


class LocalWorkspace:
    """Owns the durable local boundary that the desktop host cannot access directly."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.database_path = root / "workspace.sqlite3"
        self.youtube_requests = YouTubeRequestPolicy(self.database_path)
        self.youtube_requests.now = self.now
        self.model_gate = ModelAccountGate(self.database_path)
        # (library contract, this program's contract) when a newer program wrote it.
        self.newer_data_contract: tuple[int, int] | None = None

    @staticmethod
    def now() -> float:
        return time.time()

    @classmethod
    def open(cls, root: Path) -> LocalWorkspace:
        root.mkdir(parents=True, exist_ok=True)
        workspace = cls(root)
        workspace._initialize_database()
        return workspace

    def snapshot(self) -> LibrarySnapshot:
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT video_id, channel_title, title, url, published_at, preparation_state, failure_reason,
                       duration_seconds
                FROM candidates
                WHERE preparation_state != 'filtered' OR manuscript_version IS NOT NULL
                ORDER BY COALESCE(published_ts, 0) DESC, discovered_at DESC
                """
            ).fetchall()
            state_rows = connection.execute(
                "SELECT reading_state, COUNT(*) FROM candidates "
                "WHERE preparation_state != 'filtered' OR manuscript_version IS NOT NULL "
                "GROUP BY reading_state"
            ).fetchall()
        reading_counts = {str(state): int(count) for state, count in state_rows}
        candidates = [
            {
                "video_id": str(row[0]),
                "channel_title": str(row[1]),
                "title": str(row[2]),
                "url": str(row[3]),
                "published_at": str(row[4]),
                "preparation_state": str(row[5]),
                "failure_reason": row[6],
                "duration_seconds": row[7],
            }
            for row in rows
        ]
        return LibrarySnapshot(
            inbox=reading_counts.get("inbox", 0),
            to_read=reading_counts.get("to-read", 0),
            reading=reading_counts.get("reading", 0),
            read=reading_counts.get("read", 0),
            candidates=candidates,
        )

    def add_candidate(self, candidate: Candidate) -> bool:
        now = time.time()
        facts = {
            'published_at': candidate.published_at, 'published_ts': candidate.published_ts,
            'timing_status': candidate.timing_status, 'completion_status': candidate.completion_status,
            'actual_start_at': candidate.actual_start_at, 'actual_end_at': candidate.actual_end_at,
            'scheduled_start_at': candidate.scheduled_start_at, 'timing_checked_at': candidate.timing_checked_at,
        }
        state = self._admission(facts, now)
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            cursor = connection.execute(
                f"""INSERT OR IGNORE INTO candidates
                    (video_id, channel_id, channel_title, title, url, discovered_at,
                     preparation_state, failure_reason, {','.join(facts)})
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, {','.join('?' for _ in facts)}
                WHERE NOT EXISTS (SELECT 1 FROM excluded_channels WHERE channel_id = ?)
                  AND NOT EXISTS (SELECT 1 FROM unsubscribed_channels WHERE channel_id = ?)""",
                (candidate.video_id, candidate.channel_id, candidate.channel_title, candidate.title,
                 candidate.url, now, state, WAITING_REASONS.get(state), *facts.values(),
                 candidate.channel_id, candidate.channel_id),
            )
            inserted = cursor.rowcount > 0
            if not inserted:
                previous = connection.execute('SELECT * FROM candidates WHERE video_id=?', (candidate.video_id,)).fetchone()
                if previous and (previous['preparation_state'] in ('awaiting_timing', 'awaiting_completion')
                                 or (previous['source_observed_at'] is None and candidate.source_observed_at is not None
                                     and previous['preparation_state'] in ('queued', 'expired', 'rate_limited', 'waiting_retry', 'awaiting_classification'))):
                    # Never downgrade an identified event to ordinary publication.
                    if previous['timing_status'] == 'event' and candidate.timing_status != 'event':
                        facts.update(timing_status='event', completion_status='unverified')
                    state = self._admission({**dict(previous), **facts}, now)
                    if state == 'queued' and previous['preparation_state'] in ('rate_limited', 'waiting_retry', 'awaiting_classification'):
                        state = previous['preparation_state']
                    connection.execute(
                        f"""UPDATE candidates SET {','.join(key + '=?' for key in facts)},
                            preparation_state=?, preparation_stage=?, failure_reason=? WHERE video_id=?""",
                        (*facts.values(), state, state, WAITING_REASONS.get(state), candidate.video_id),
                    )
            connection.execute(
                """UPDATE candidates SET source_observed_at=COALESCE(source_observed_at,?),
                   source_observed_from=COALESCE(source_observed_from,?),
                   source_observed_until=COALESCE(source_observed_until,?),
                   playlist_added_at=COALESCE(playlist_added_at,?) WHERE video_id=?""",
                (candidate.source_observed_at, candidate.source_observed_from,
                 candidate.source_observed_until, candidate.playlist_added_at, candidate.video_id),
            )
        return inserted

    def pending_manual_timing(self, now: float) -> list[str]:
        with sqlite3.connect(self.database_path) as connection:
            return [row[0] for row in connection.execute("""SELECT video_id FROM candidates WHERE request_kind='manual'
                AND preparation_state IN ('awaiting_timing','awaiting_completion')
                AND COALESCE(timing_checked_at,0) <= ? ORDER BY COALESCE(timing_checked_at,0),video_id LIMIT 10""", (now - 300,))]

    def refresh_manual_timing(self, candidate: Candidate) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute("SELECT * FROM candidates WHERE video_id=? AND request_kind='manual' AND preparation_state IN ('awaiting_timing','awaiting_completion')", (candidate.video_id,)).fetchone()
            if row is None:
                return  # Cancellation/deletion after the external request always wins.
            facts = {key: getattr(candidate, key) for key in ('published_at','published_ts','timing_status',
                'completion_status','actual_start_at','actual_end_at','scheduled_start_at','timing_checked_at')}
            if row['timing_status'] == 'event' and candidate.timing_status != 'event':
                facts.update(timing_status='event', completion_status='unverified')
            state = self._admission({**dict(row), **facts}, time.time())
            connection.execute(f"UPDATE candidates SET {','.join(key+'=?' for key in facts)},preparation_state=?,preparation_stage=?,failure_reason=? WHERE video_id=?",
                (*facts.values(),state,state,WAITING_REASONS.get(state),candidate.video_id))

    def manual_receipt(self, request_id: str) -> str | None:
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute("SELECT video_id FROM manual_video_requests WHERE request_id=?", (request_id,)).fetchone()
        return row[0] if row else None

    def accept_manual_request(self, video_id: str, request_id: str, *, candidate: Candidate | None = None,
                              action: str | None = None) -> None:
        """Commit identity, intent and receipt together; duplicates never reset work."""
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            receipt = connection.execute("SELECT video_id FROM manual_video_requests WHERE request_id=?", (request_id,)).fetchone()
            if receipt:
                if receipt[0] != video_id:
                    raise ValueError("提交标识已被其他视频使用，请重新提交。")
                return
            row = connection.execute("SELECT * FROM candidates WHERE video_id=?", (video_id,)).fetchone()
            if row is None:
                if candidate is None or action:
                    raise ValueError("视频记录不存在，请重新检查链接。")
                facts = {key: getattr(candidate, key) for key in ('published_at', 'published_ts', 'timing_status',
                    'completion_status', 'actual_start_at', 'actual_end_at', 'scheduled_start_at', 'timing_checked_at')}
                state = self._admission({**facts, 'request_kind': 'manual'}, time.time())
                connection.execute(f"""INSERT INTO candidates(video_id,channel_id,channel_title,title,url,discovered_at,
                    request_kind,preparation_state,failure_reason,{','.join(facts)}) VALUES(?,?,?,?,?,?,'manual',?,?,{','.join('?' for _ in facts)})""",
                    (video_id,candidate.channel_id,candidate.channel_title,candidate.title,candidate.url,time.time(),
                     state,WAITING_REASONS.get(state),*facts.values()))
            elif action:
                expected = {'restore': ('cancelled',), 'retry': ('failed', 'unavailable')}.get(action)
                if not expected or row['preparation_state'] not in expected or row['manuscript_version']:
                    raise ValueError("视频状态已变化，请查看现有任务或文档。")
                connection.execute("""UPDATE candidates SET request_kind='manual',preparation_state='queued',
                    preparation_stage='queued',failure_reason=NULL,retry_at=NULL,rate_limit_attempts=0,
                    caption_first_absent_at=NULL,caption_checks=0,caption_checks_ended_at=NULL,
                    preparation_completed_at=NULL WHERE video_id=?""", (video_id,))
                self._recheck_waiting(connection)
                self._activity_event(connection, video_id, 'restored' if action == 'restore' else 'queued')
            elif not row['manuscript_version'] and row['preparation_state'] not in ('cancelled','failed','unavailable','filtered'):
                connection.execute("UPDATE candidates SET request_kind='manual' WHERE video_id=?", (video_id,))
                if row['preparation_state'] == 'expired':
                    state = self._admission({**dict(row), 'request_kind':'manual'}, time.time())
                    connection.execute("UPDATE candidates SET preparation_state=?,preparation_stage=?,failure_reason=? WHERE video_id=?",
                                       (state,state,WAITING_REASONS.get(state),video_id))
            connection.execute("INSERT INTO manual_video_requests VALUES (?,?,?)", (request_id, video_id, time.time()))

    def excluded_channels(self) -> list[str]:
        with sqlite3.connect(self.database_path) as connection:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT channel_id FROM excluded_channels ORDER BY channel_id"
                )
            ]

    def set_excluded_channels(self, channels: list[str]) -> None:
        # Share SQLite's write ordering with queue claims. Already-claimed work
        # completes; existing documents are never removed by subscription settings.
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DELETE FROM excluded_channels")
            connection.executemany(
                "INSERT INTO excluded_channels (channel_id) VALUES (?)",
                [(channel,) for channel in sorted(set(channels))],
            )

    def claim_next_queued_asset(self, model_account: str | None = None, *, acquisition_allowed=True) -> dict[str, object] | None:
        """Atomically honour drain pause and claim exactly one runnable asset.

        Waiting work yields: a subtitle cooldown only withholds work that still
        needs YouTube, and a model account limit only withholds work whose next
        step is that model. Retries keep their original place in line.
        """
        now = time.time()
        model_limited = self.model_gate.active(model_account, now) is not None
        with sqlite3.connect(self.database_path, isolation_level=None) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            held = connection.execute(
                "SELECT value FROM workspace_meta WHERE key = 'youtube_cutover'"
            ).fetchone()
            if (held and json.loads(held[0]).get("held")) or _recorded_contract(connection) > YOUTUBE_DATA_CONTRACT:
                connection.commit()
                return None
            paused = connection.execute(
                "SELECT value FROM workspace_meta WHERE key = 'drain_paused'"
            ).fetchone()
            captions_cooling = (
                self.youtube_requests._read(connection)["cooldown_until"] > self.youtube_requests.now()
            )
            recovery = self.youtube_requests._read(connection).get("failure_kind")
            if recovery and connection.execute(
                "SELECT 1 FROM candidates c WHERE preparation_state = 'acquiring' "
                "AND NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = c.video_id) LIMIT 1"
            ).fetchone():
                captions_cooling = True
            self._recheck_waiting(connection)
            row = connection.execute(
                """
                SELECT video_id, channel_id, channel_title, title, url, published_at,
                       manuscript_version, shorts_status
                FROM candidates c WHERE (preparation_state IN ('queued', 'rate_limited') OR
                    (preparation_state = 'awaiting_classification' AND shorts_retry_at <= ?) OR
                    (preparation_state = 'waiting_retry' AND COALESCE(retry_at, 0) <= ?))
                  AND (? = 0 OR request_kind = 'manual')
                  AND (? = 0 OR (EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = c.video_id)
                                 AND (shorts_status = 'video' OR manuscript_version IS NOT NULL)))
                  AND (? = 0 OR NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = c.video_id))
                  AND (request_kind = 'manual' OR (channel_id NOT IN (SELECT channel_id FROM excluded_channels)
                  AND channel_id NOT IN (SELECT channel_id FROM unsubscribed_channels)))
                  AND (shorts_status != 'short' OR manuscript_version IS NOT NULL)
                ORDER BY discovered_at ASC LIMIT 1
                """,
                (now, now, int(paused is None or paused[0] == "1"), int(captions_cooling or not acquisition_allowed), int(model_limited)),
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            stage = (
                "checking"
                if row["manuscript_version"] is None and row["shorts_status"] != "video"
                else "acquiring"
            )
            connection.execute(
                """
                UPDATE candidates SET preparation_state = 'acquiring', failure_reason = NULL,
                    preparation_started_at = ?, preparation_completed_at = NULL,
                    preparation_stage = ?, stage_updated_at = ?
                WHERE video_id = ?
                """,
                (time.time(), stage, time.time(), row["video_id"]),
            )
            self._activity_event(connection, row["video_id"], stage)
            connection.execute("COMMIT")
        return dict(row)

    @staticmethod
    def _admission(facts: dict[str, Any], now: float) -> str:
        return preparation_admission(
            facts['published_at'], facts['published_ts'], now, facts['timing_status'],
            facts.get('completion_status', 'unverified'), facts.get('actual_end_at'),
            age_exempt=facts.get('commenced_at') is not None or facts.get('request_kind') == 'manual',
        )

    def pending_video_timing(self, channel_id: str, limit: int) -> list[dict[str, Any]]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(
                """SELECT * FROM candidates WHERE channel_id=?
                   AND preparation_state IN ('awaiting_timing', 'awaiting_completion')
                   ORDER BY COALESCE(timing_checked_at, 0), video_id LIMIT ?""", (channel_id, limit))]

    def _expire_caption_checks(self, connection: sqlite3.Connection) -> None:
        now = self.now()
        expired = connection.execute(
            """SELECT c.video_id FROM candidates c WHERE caption_first_absent_at IS NOT NULL
               AND caption_first_absent_at + 86400 < ?
               AND preparation_state IN ('queued', 'waiting_retry', 'rate_limited',
                                         'awaiting_classification', 'acquiring')
               AND NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = c.video_id)""",
            (now,),
        ).fetchall()
        reason = "字幕自动检查已结束，需要时请显式重试。已有素材保留。"
        for row in expired:
            connection.execute(
                """UPDATE candidates SET preparation_state = 'failed', preparation_stage = 'failed',
                   caption_checks_ended_at = ?, retry_at = NULL, failure_reason = ?,
                   preparation_completed_at = ?, stage_updated_at = ? WHERE video_id = ?""",
                (now, reason, now, now, row[0]),
            )
            self._activity_event(connection, row[0], "failed", reason)

    def caption_check_allowed(self, video_id: str) -> bool:
        """Recheck the finite window immediately before external acquisition I/O."""
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_caption_checks(connection)
            row = connection.execute("SELECT preparation_state FROM candidates WHERE video_id = ?",
                                     (video_id,)).fetchone()
            return bool(row and row[0] in {"acquiring", "generating"})

    def _recheck_waiting(self, connection: sqlite3.Connection) -> None:
        self._expire_caption_checks(connection)
        cursor = connection.execute(
            """SELECT * FROM candidates
               WHERE (commenced_at IS NULL OR timing_status != 'publication')
                 AND preparation_state IN ('queued', 'rate_limited', 'waiting_retry', 'awaiting_classification')"""
        )
        names = [column[0] for column in cursor.description]
        for row in cursor.fetchall():
            facts = dict(zip(names, row, strict=True))
            state = self._admission(facts, time.time())
            if state != 'queued':
                connection.execute(
                    "UPDATE candidates SET preparation_state = ?, preparation_stage = ?, failure_reason = ? WHERE video_id = ?",
                    (state, state, WAITING_REASONS[state], facts['video_id']),
                )

    def await_video_timing(self, video_id: str) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """UPDATE candidates SET preparation_state = 'awaiting_timing',
                   preparation_stage = 'awaiting_timing', timing_status = 'event', completion_status = 'unverified',
                   failure_reason = '直播或首映的结束时间尚未可靠核实，等待确认。'
                   WHERE video_id = ?""", (video_id,),
            )

    def commence_preparation(self, video_id: str) -> bool:
        """Record the first real work boundary, never a reservation or classification."""
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            self._expire_caption_checks(connection)
            row = connection.execute(
                "SELECT * FROM candidates WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            if row is None or row['preparation_state'] not in {'acquiring', 'generating'}:
                return False
            if row['commenced_at'] is not None and row['timing_status'] == 'publication':
                return True
            state = self._admission(dict(row), time.time())
            if state != 'queued':
                connection.execute(
                    "UPDATE candidates SET preparation_state = ?, preparation_stage = ?, failure_reason = ? WHERE video_id = ?",
                    (state, state, WAITING_REASONS[state], video_id),
                )
                self._activity_event(connection, video_id, state, WAITING_REASONS[state])
                return False
            if row['commenced_at'] is not None:
                return True
            connection.execute('UPDATE candidates SET commenced_at = ? WHERE video_id = ?', (time.time(), video_id))
            self._activity_event(connection, video_id, 'commenced', '已实际开工，同次自动任务可跨窗口续办。')
            return True

    def record_acquisition_failure(self, video_id: str, kind: str) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("UPDATE candidates SET acquisition_failure_kind = ? WHERE video_id = ?",
                               (kind, video_id))

    def defer_rate_limited(self, video_id: str, *, attempted: bool = True) -> None:
        # Service restrictions never spend individual video/model retry budgets.
        state = self.youtube_requests.snapshot()
        self.defer_retry(video_id, Failure("transient", state.get("reason", "YouTube 冷却中。"),
                                          state["cooldown_until"]), charge=False)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("UPDATE candidates SET preparation_state = 'rate_limited', "
                               "preparation_stage = 'rate_limited' WHERE video_id = ? "
                               "AND preparation_state = 'waiting_retry'", (video_id,))

    def defer_retry(self, video_id: str, failure: Failure, *, charge: bool = True) -> str:
        """Release the processing position until the next attempt is due.

        Charged failures spend the task's bounded automatic budget and back off;
        uncharged waits (a restricted account) only honour the given deadline.
        """
        if failure.kind == "caption_pending":
            return self.defer_caption_absence(video_id)
        now = time.time()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT rate_limit_attempts FROM candidates WHERE video_id = ? "
                "AND preparation_state IN ('acquiring', 'generating')", (video_id,),
            ).fetchone()
            if row is None:
                return ""
            attempts = row[0] + int(charge)
            if charge and attempts >= MAX_AUTOMATIC_ATTEMPTS:
                state, retry_at, completed = "failed", None, now
                reason = f"已自动尝试 {attempts} 次仍未成功，需手动重试。已有素材保留。{failure.reason}"
            else:
                state, completed, reason = "waiting_retry", None, failure.reason
                retry_at = max(failure.retry_at or 0, now + retry_delay(attempts) if charge else now)
            connection.execute(
                """UPDATE candidates SET preparation_state = ?, preparation_stage = ?, failure_reason = ?,
                   rate_limit_attempts = ?, retry_at = ?, stage_updated_at = ?, preparation_completed_at = ?
                   WHERE video_id = ?""",
                (state, state, reason, attempts, retry_at, now, completed, video_id),
            )
            self._activity_event(connection, video_id, state, reason)
        return state

    def defer_caption_absence(self, video_id: str) -> str:
        now = self.now()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT caption_first_absent_at, caption_checks FROM candidates "
                "WHERE video_id = ? AND preparation_state = 'acquiring'", (video_id,)
            ).fetchone()
            if row is None:
                return ""
            first = row[0] if row[0] is not None else now
            checks = row[1] + int(row[0] is not None)
            # Missed checkpoints are not replayed: one check at the current time
            # either schedules the next future checkpoint or ends the finite wait.
            future = [first + offset for offset in (3600, 21600, 86400) if first + offset > now]
            retry_at = future[0] if future else None
            state = "waiting_retry" if retry_at is not None else "failed"
            reason = ("字幕尚未提供，等待下一次自动检查。" if retry_at is not None
                      else "字幕自动检查已结束，需要时请显式重试。已有素材保留。")
            connection.execute(
                """UPDATE candidates SET caption_first_absent_at = ?, caption_checks = ?,
                   caption_checks_ended_at = ?, retry_at = ?, preparation_state = ?,
                   preparation_stage = ?, failure_reason = ?, stage_updated_at = ?,
                   preparation_completed_at = ? WHERE video_id = ?""",
                (first, checks, now if retry_at is None else None, retry_at, state, state,
                 reason, now, now if retry_at is None else None, video_id))
            self._activity_event(connection, video_id, state, reason)
        return state

    def limit_model_account(self, account: str | None, failure: Failure) -> Failure:
        until = self.model_gate.limit(account, failure, time.time())
        return Failure(failure.kind, failure.reason, until)

    def model_account_limit(self, account: str | None) -> dict | None:
        return self.model_gate.active(account, time.time())

    def model_account_succeeded(self, account: str | None) -> None:
        self.model_gate.succeeded(account, time.time())

    def record_shorts_classification(self, video_id: str, is_short: bool | None) -> None:
        if is_short is not None and type(is_short) is not bool:
            raise ValueError("classification must be True, False or None")
        now = time.time()
        if is_short is None:
            status, state = "unknown", "awaiting_classification"
        elif is_short:
            status, state = "short", "filtered"
        else:
            status, state = "video", "acquiring"
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.execute(
                """UPDATE candidates SET shorts_status = ?, shorts_checked_at = ?,
                   shorts_retry_at = ?, preparation_state = ?, preparation_stage = ?,
                   stage_updated_at = ?, preparation_completed_at = ?, failure_reason = NULL
                   WHERE video_id = ? AND preparation_state = 'acquiring'
                     AND manuscript_version IS NULL""",
                (
                    status, now,
                    now + CLASSIFICATION_RETRY_SECONDS if is_short is None else None,
                    state, state, now, now if is_short is True else None, video_id,
                ),
            )
            if cursor.rowcount:
                self._activity_event(connection, video_id, state)

    def set_preparation_state(
        self, video_id: str, state: str, failure_reason: str | None = None
    ) -> None:
        if state not in {"queued", "acquiring", "generating", "ready", "unavailable", "failed"}:
            raise ValueError(f"unknown preparation state: {state}")
        completed_at = time.time() if state in {"ready", "unavailable", "failed"} else None
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE candidates
                SET preparation_state = ?, failure_reason = ?,
                    preparation_started_at = CASE WHEN ? IN ('acquiring', 'generating')
                        THEN COALESCE(preparation_started_at, ?) ELSE preparation_started_at END,
                    preparation_completed_at = ?, preparation_stage = ?, stage_updated_at = ?
                WHERE video_id = ?
                """,
                (
                    state,
                    failure_reason,
                    state,
                    time.time(),
                    completed_at,
                    state,
                    time.time(),
                    video_id,
                ),
            )
            if cursor.rowcount:
                self._activity_event(connection, video_id, state)
        if cursor.rowcount != 1:
            raise KeyError(f"unknown asset: {video_id}")

    def queue_regeneration(self, video_id: str) -> None:
        # Serialize with automatic claims: never erase the checkpoint of a job
        # which became active between the user's inspection and this request.
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT preparation_state FROM candidates WHERE video_id = ?", (video_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown asset: {video_id}")
            if row[0] in {"acquiring", "generating"}:
                raise ValueError("资料正在生成，请等待本次处理完成。")
            if row[0] == "cancelled":
                raise ValueError("此视频已取消处理，请先在处理进度中恢复。")
            if row[0] == "filtered":
                raise ValueError("此视频是 YouTube Shorts，已排除自动处理。")
            self.translation_checkpoint(video_id).unlink(missing_ok=True)
            connection.execute(
                """
                UPDATE candidates
                SET preparation_state = 'queued', failure_reason = NULL, retry_at = NULL,
                    rate_limit_attempts = 0, commenced_at = NULL, request_kind = 'manual',
                    caption_first_absent_at = NULL, caption_checks = 0, caption_checks_ended_at = NULL,
                    preparation_started_at = NULL, preparation_completed_at = NULL
                WHERE video_id = ?
                """,
                (video_id,),
            )

    def retry_failed(self, video_id: str | None = None, *, reason: str | None = None) -> int:
        statement = """
            UPDATE candidates SET preparation_state = 'queued', failure_reason = NULL,
                rate_limit_attempts = 0, retry_at = NULL,
                caption_first_absent_at = NULL, caption_checks = 0, caption_checks_ended_at = NULL,
                preparation_started_at = NULL, preparation_completed_at = NULL
            WHERE preparation_state = 'failed'
        """
        values: tuple[object, ...] = ()
        if video_id is not None:
            statement += " AND video_id = ?"
            values = (video_id,)
        if reason is not None:
            statement += " AND failure_reason = ?"
            values += (reason,)
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.execute(statement, values)
            self._recheck_waiting(connection)
        return cursor.rowcount

    @staticmethod
    def _console_entry(connection: sqlite3.Connection, video_id: str, message: str) -> None:
        connection.execute(
            "INSERT INTO preparation_console (video_id, message, occurred_at) VALUES (?, ?, ?)",
            (video_id, message[:4000], time.time()),
        )
        connection.execute(
            "DELETE FROM preparation_console WHERE sequence <= "
            "(SELECT MAX(sequence) - 500 FROM preparation_console)"
        )

    def report_console(self, video_id: str, message: str) -> None:
        with sqlite3.connect(self.database_path) as connection:
            self._console_entry(connection, video_id, message)

    @staticmethod
    def _activity_event(connection: sqlite3.Connection, video_id: str, stage: str, detail: str = "") -> None:
        now = time.time()
        connection.execute(
            "INSERT INTO preparation_events (video_id, stage, detail, occurred_at) VALUES (?, ?, ?, ?)",
            (video_id, stage, detail, now),
        )
        # Acceptance evidence outlives the console and a later asset deletion.
        acceptance.record_task(connection, video_id, stage, now)
        LocalWorkspace._console_entry(connection, video_id, f"[{stage}] {detail}".rstrip())

    def change_queue(self, video_ids: list[str], *, restore: bool = False) -> dict[str, object]:
        """Serialize cancellation with worker claims; report races per item."""
        expected = ("cancelled",) if restore else ("queued", "awaiting_classification", "rate_limited", "waiting_retry", "expired", "awaiting_timing", "awaiting_completion")
        target = "queued" if restore else "cancelled"
        changed, skipped = [], []
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for video_id in dict.fromkeys(video_ids):
                cursor = connection.execute(
                    f"""UPDATE candidates SET preparation_state = ?, preparation_stage = NULL,
                        stage_updated_at = ?, commenced_at = NULL, preparation_started_at = NULL,
                        preparation_completed_at = NULL, failure_reason = NULL, request_kind = 'automatic'
                        WHERE video_id = ? AND preparation_state IN ({",".join("?" for _ in expected)})""",
                    (target, time.time(), video_id, *expected),
                )
                if cursor.rowcount:
                    changed.append(video_id)
                    self._activity_event(
                        connection, video_id, "restored" if restore else "cancelled"
                    )
                else:
                    row = connection.execute(
                        "SELECT preparation_state FROM candidates WHERE video_id = ?", (video_id,)
                    ).fetchone()
                    skipped.append({"video_id": video_id, "state": row[0] if row else "missing"})
            self._recheck_waiting(connection)
        return {"changed": changed, "skipped": skipped}

    def report_stage(self, video_id: str, stage: str, detail: str = "") -> None:
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.execute(
                """UPDATE candidates SET preparation_stage = ?, stage_updated_at = ?
                   WHERE video_id = ? AND preparation_state IN ('acquiring', 'generating')""",
                (stage, time.time(), video_id),
            )
            if cursor.rowcount:
                self._activity_event(connection, video_id, stage, detail)

    def activity_items(
        self, state: str = "queued", offset: int = 0, limit: int = 50
    ) -> dict[str, object]:
        if state not in {"queued", "cancelled", "failed", "unavailable", "ready", "awaiting_classification", "filtered", "rate_limited", "waiting_retry", "expired", "awaiting_timing", "awaiting_completion"}:
            raise ValueError("无效的处理状态。")
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
            raise ValueError("无效的分页范围。")
        where = "preparation_state = ?"
        if state in {"queued", "awaiting_classification", "rate_limited", "waiting_retry"}:
            where += " AND (request_kind = 'manual' OR channel_id NOT IN (SELECT channel_id FROM excluded_channels))"
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            total = connection.execute(
                f"SELECT COUNT(*) FROM candidates WHERE {where}", (state,)
            ).fetchone()[0]
            rows = connection.execute(
                f"""SELECT video_id, title, channel_title, preparation_state, failure_reason,
                    manuscript_version, published_at, duration_seconds, commenced_at, request_kind, playlist_added_at, source_observed_at,
                    retry_at, rate_limit_attempts AS automatic_attempts, acquisition_failure_kind,
                    caption_first_absent_at, caption_checks, caption_checks_ended_at FROM candidates WHERE {where}
                    ORDER BY discovered_at ASC, video_id ASC LIMIT ? OFFSET ?""",
                (state, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total}

    def set_reading_state(self, video_id: str, state: str) -> None:
        if state not in {"inbox", "to-read", "reading", "read"}:
            raise ValueError(f"unknown reading state: {state}")
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.execute(
                "UPDATE candidates SET reading_state = ? WHERE video_id = ?", (state, video_id)
            )
        if cursor.rowcount != 1:
            raise KeyError(f"unknown asset: {video_id}")

    def record_video_duration(self, video_id: str, duration_seconds: float | None) -> None:
        """Retain source duration independently of subtitle and manuscript success."""
        if (isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float))
                or not math.isfinite(duration_seconds) or duration_seconds <= 0):
            return
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE candidates SET duration_seconds = ? WHERE video_id = ?",
                (duration_seconds, video_id),
            )

    def store_transcript(self, video_id: str, *, language: str, srt_text: str) -> str:
        asset_dir = self._asset_directory(video_id)
        asset_dir.mkdir(parents=True, exist_ok=True)
        path = asset_dir / "transcript.srt"
        path.write_text(srt_text, encoding="utf-8")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO transcripts (video_id, language, path, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                  language=excluded.language, path=excluded.path, content=excluded.content,
                  created_at=excluded.created_at
                """,
                (video_id, language, str(path), srt_text, time.time()),
            )
            self._activity_event(connection, video_id, "transcript_ready")
        return str(path)

    def stage_timings(self, video_id: str) -> dict[str, float | None]:
        """Durable per-stage times for acceptance of delivery latency.

        Caption wait is time spent yielding before the transcript was retained;
        processing is time holding a processing position; readable is when the
        current manuscript became available.
        """
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                """SELECT c.discovered_at, c.commenced_at, t.created_at, m.created_at FROM candidates c
                   LEFT JOIN transcripts t ON t.video_id = c.video_id
                   LEFT JOIN manuscripts m ON m.video_id = c.video_id AND m.version = c.manuscript_version
                   WHERE c.video_id = ?""", (video_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown asset: {video_id}")
            events = connection.execute(
                "SELECT stage, occurred_at FROM preparation_events WHERE video_id = ? ORDER BY sequence",
                (video_id,),
            ).fetchall()
        discovered, commenced, transcript_at, readable_at = row
        caption_wait = processing = 0.0
        stage, since, transcript_seen = "queued", discovered, False
        for next_stage, at in [*events, (None, readable_at or time.time())]:
            span = max(0.0, at - since)
            if stage in YIELDING_STAGES:
                caption_wait += 0.0 if transcript_seen else span
            elif stage not in PARKED_STAGES:
                processing += span
            if next_stage is None:
                break
            transcript_seen = transcript_seen or next_stage == "transcript_ready"
            stage, since = next_stage, at
        return {
            "discovered_at": discovered, "commenced_at": commenced,
            "transcript_ready_at": transcript_at, "readable_at": readable_at,
            "caption_wait_seconds": caption_wait, "processing_seconds": processing,
        }

    def save_manuscript(
        self,
        video_id: str,
        markdown: str,
        *,
        generator: str,
        transcript_characters: int,
        translation: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM manuscripts WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            version = int(row[0])
        directory = self._asset_directory(video_id) / f"manuscript-v{version}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "manuscript.md"
        path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        if translation is not None:
            (directory / "translation.md").write_text(translation.rstrip() + "\n", encoding="utf-8")
        if provenance is not None:
            (directory / "translation-run.json").write_text(
                json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        created_at = time.time()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO manuscripts (video_id, version, path, markdown, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (video_id, version, str(path), markdown.rstrip() + "\n", created_at),
            )
            connection.execute(
                """
                UPDATE candidates SET manuscript_version = ?, manuscript_path = ?
                WHERE video_id = ?
                """,
                (version, str(path), video_id),
            )
            connection.execute(
                """
                INSERT INTO generation_records
                    (video_id, manuscript_version, generator, transcript_characters,
                     manuscript_characters, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    version,
                    generator,
                    transcript_characters,
                    len(markdown.rstrip() + "\n"),
                    created_at,
                ),
            )
        return {"version": version, "path": str(path), "created_at": created_at}

    def generation_records(self, video_id: str) -> list[dict[str, object]]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT manuscript_version, generator, transcript_characters, manuscript_characters, created_at
                FROM generation_records WHERE video_id = ? ORDER BY manuscript_version
                """,
                (video_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self, video_id: str) -> str | None:
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT summary FROM candidates WHERE video_id = ?", (video_id,)
            ).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def save_summary(self, video_id: str, text: str) -> str:
        if not text or len(text) > 120:
            raise ValueError("摘要必须为 1–120 个字符。")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE candidates SET summary = ? WHERE video_id = ? AND summary IS NULL",
                (text, video_id),
            )
            row = connection.execute(
                "SELECT summary FROM candidates WHERE video_id = ?", (video_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown asset: {video_id}")
        return str(row[0])

    def asset(self, video_id: str) -> dict[str, object]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT c.video_id, c.channel_id, c.channel_title, c.title, c.url, c.published_at, c.playlist_added_at, c.source_observed_at, c.source_observed_from, c.source_observed_until, c.discovered_at, c.published_ts, c.timing_status, c.completion_status, c.actual_start_at, c.actual_end_at, c.scheduled_start_at, c.timing_checked_at,
                       c.preparation_state, c.failure_reason, c.reading_state, c.manuscript_version, c.request_kind,
                       c.manuscript_path, c.preparation_completed_at, c.duration_seconds,
                       t.path AS transcript_path
                FROM candidates c LEFT JOIN transcripts t ON t.video_id = c.video_id
                WHERE c.video_id = ?
                """,
                (video_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown asset: {video_id}")
        return dict(row)

    def assets(
        self,
        *,
        query: str = "",
        include_transcript: bool = False,
        documents_only: bool = False,
        unread_only: bool = False,
        reading_state: str | None = None,
        channel_id: str | None = None,
        preparation_state: str | None = None,
        published_after: float | None = None,
        published_before: float | None = None,
    ) -> list[dict[str, object]]:
        clauses: list[str] = ["(c.preparation_state != 'filtered' OR c.manuscript_version IS NOT NULL)"]
        values: list[object] = []
        if documents_only:
            clauses.append("c.manuscript_version IS NOT NULL")
        if unread_only:
            clauses.append("c.reading_state != 'read'")
        if reading_state:
            clauses.append("c.reading_state = ?")
            values.append(reading_state)
        if channel_id:
            clauses.append("c.channel_id = ?")
            values.append(channel_id)
        if preparation_state:
            clauses.append("c.preparation_state = ?")
            values.append(preparation_state)
        if published_after is not None:
            clauses.append("c.published_ts >= ?")
            values.append(published_after)
        if published_before is not None:
            clauses.append("c.published_ts <= ?")
            values.append(published_before)
        if query.strip():
            escaped = f"%{query.strip()}%"
            text = "c.title LIKE ? COLLATE NOCASE OR c.channel_title LIKE ? COLLATE NOCASE OR m.markdown LIKE ? COLLATE NOCASE"
            values.extend([escaped, escaped, escaped])
            if include_transcript:
                text += " OR t.content LIKE ? COLLATE NOCASE"
                values.append(escaped)
            clauses.append(f"({text})")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""
                SELECT c.video_id, c.channel_id, c.channel_title, c.title, c.url, c.published_at, c.playlist_added_at, c.source_observed_at, c.source_observed_from, c.source_observed_until, c.discovered_at, c.published_ts, c.timing_status, c.completion_status, c.actual_start_at, c.actual_end_at, c.scheduled_start_at, c.timing_checked_at,
                       c.preparation_state, c.failure_reason, c.reading_state, c.manuscript_version,
                       c.manuscript_path, c.preparation_completed_at, c.duration_seconds,
                       SUBSTR(m.markdown, 1, 400) AS excerpt,
                       LENGTH(m.markdown) AS manuscript_characters,
                       CASE WHEN t.video_id IS NULL THEN 0 ELSE 1 END AS transcript_available
                FROM candidates c
                LEFT JOIN manuscripts m ON m.video_id = c.video_id AND m.version = c.manuscript_version
                LEFT JOIN transcripts t ON t.video_id = c.video_id
                {where}
                ORDER BY CASE WHEN ? = '' THEN 0 ELSE
                    (CASE WHEN c.title LIKE ? COLLATE NOCASE THEN 3 ELSE 0 END +
                     CASE WHEN c.channel_title LIKE ? COLLATE NOCASE THEN 2 ELSE 0 END +
                     CASE WHEN m.markdown LIKE ? COLLATE NOCASE THEN 1 ELSE 0 END)
                END DESC, COALESCE(c.published_ts, 0) DESC, c.discovered_at DESC
                """,
                [
                    *values,
                    query.strip(),
                    f"%{query.strip()}%",
                    f"%{query.strip()}%",
                    f"%{query.strip()}%",
                ],
            ).fetchall()
        return [dict(row) for row in rows]

    def document(self, video_id: str) -> dict[str, object]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT m.path, m.markdown, m.version FROM manuscripts m
                JOIN candidates c ON c.video_id = m.video_id AND c.manuscript_version = m.version
                WHERE m.video_id = ?
                """,
                (video_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"asset has no manuscript: {video_id}")
        return {
            "path": str(row["path"]),
            "markdown": str(row["markdown"]),
            "version": row["version"],
            "translation_path": str(Path(row["path"]).with_name("translation.md"))
            if Path(row["path"]).with_name("translation.md").exists()
            else None,
        }

    def recover_interrupted_preparations(self) -> None:
        """Called once by the owning host on startup, not on every workspace read."""
        with sqlite3.connect(self.database_path) as connection:
            if self.youtube_requests._read(connection)["cooldown_until"] > self.youtube_requests.now():
                connection.execute(
                    """UPDATE candidates SET preparation_state = 'rate_limited',
                       preparation_stage = 'rate_limited', stage_updated_at = ?,
                       failure_reason = 'YouTube 请求处于冷却中，结束后自动重试。'
                       WHERE preparation_state = 'acquiring'""",
                    (time.time(),),
                )
            connection.execute(
                """UPDATE candidates SET preparation_state = 'awaiting_classification',
                   preparation_stage = 'awaiting_classification', shorts_retry_at = ?,
                   stage_updated_at = ?, failure_reason = NULL
                   WHERE preparation_state = 'acquiring' AND preparation_stage = 'checking'
                     AND shorts_status = 'unknown' AND manuscript_version IS NULL""",
                (time.time() + CLASSIFICATION_RETRY_SECONDS, time.time()),
            )
            # An interruption is not a failure of the task: it keeps its budget,
            # retained stages and place in line. First work still rechecks admission.
            connection.execute(
                """UPDATE candidates SET preparation_state = 'queued', preparation_stage = NULL,
                   failure_reason = NULL WHERE preparation_state IN ('acquiring', 'generating')"""
            )

    def translation_checkpoint(self, video_id: str) -> Path:
        return self._asset_directory(video_id) / "translation-attempt.json"

    def _usable_transcript(self, video_id: str, language: str, path: str, content: str) -> bool:
        from youtube_strataread.downloader import cues_to_lines, load_cues
        expected = self._asset_directory(video_id) / 'transcript.srt'
        try:
            return (bool(language) and Path(path).resolve() == expected.resolve()
                    and expected.read_text(encoding='utf-8') == content
                    and bool("\n".join(cues_to_lines(load_cues(content))).strip()))
        except (OSError, ValueError, UnicodeError, TypeError):
            return False

    def retained_transcript(self, video_id: str) -> dict[str, str] | None:
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT language, path, content FROM transcripts WHERE video_id = ?", (video_id,)
            ).fetchone()
        if row and self._usable_transcript(video_id, *row):
            return {"language": row[0], "content": row[2]}
        return None

    def delete_asset(self, video_id: str) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DELETE FROM preparation_events WHERE video_id = ?", (video_id,))
            connection.execute("DELETE FROM preparation_console WHERE video_id = ?", (video_id,))
            connection.execute("DELETE FROM generation_records WHERE video_id = ?", (video_id,))
            connection.execute("DELETE FROM manuscripts WHERE video_id = ?", (video_id,))
            connection.execute("DELETE FROM transcripts WHERE video_id = ?", (video_id,))
            cursor = connection.execute("DELETE FROM candidates WHERE video_id = ?", (video_id,))
        if cursor.rowcount != 1:
            raise KeyError(f"unknown asset: {video_id}")
        shutil.rmtree(self._asset_directory(video_id), ignore_errors=True)

    def activity(self) -> dict[str, object]:
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(
                """SELECT preparation_state, COUNT(*) FROM candidates
                WHERE preparation_state NOT IN ('queued', 'awaiting_classification', 'rate_limited', 'waiting_retry') OR request_kind='manual' OR channel_id NOT IN
                    (SELECT channel_id FROM excluded_channels)
                GROUP BY preparation_state"""
            ).fetchall()
            document_count = connection.execute(
                "SELECT COUNT(*) FROM candidates WHERE manuscript_version IS NOT NULL"
            ).fetchone()[0]
            transcript_characters = connection.execute(
                "SELECT COALESCE(SUM(LENGTH(content)), 0) FROM transcripts"
            ).fetchone()[0]
            manuscript_characters = connection.execute(
                "SELECT COALESCE(SUM(LENGTH(markdown)), 0) FROM manuscripts"
            ).fetchone()[0]
            failure_rows = connection.execute(
                """
                SELECT video_id, title, preparation_state, failure_reason FROM candidates
                WHERE preparation_state IN ('failed', 'unavailable')
                ORDER BY preparation_completed_at DESC LIMIT 10
                """
            ).fetchall()
            connection.row_factory = sqlite3.Row
            current = [
                dict(row)
                for row in connection.execute(
                    """SELECT video_id, title, channel_title, preparation_state, preparation_stage,
                   preparation_started_at, stage_updated_at, published_at, duration_seconds, commenced_at FROM candidates
                   WHERE preparation_state IN ('acquiring', 'generating')
                   ORDER BY preparation_started_at"""
                )
            ]
            for item in current:
                item["observed_stages"] = [
                    row[0]
                    for row in connection.execute(
                        """SELECT DISTINCT stage FROM preparation_events
                       WHERE video_id = ? AND occurred_at >= ?""",
                        (item["video_id"], item["preparation_started_at"] or 0),
                    )
                ]
            events = [
                dict(row)
                for row in connection.execute(
                    """SELECT e.sequence, e.video_id, c.title, e.stage, e.detail, e.occurred_at
                   FROM preparation_events e JOIN candidates c ON c.video_id = e.video_id
                   ORDER BY e.sequence DESC LIMIT 60"""
                )
            ]
            console = [dict(row) for row in connection.execute(
                "SELECT sequence, video_id, message, occurred_at FROM preparation_console ORDER BY sequence"
            )]
        counts = {str(state): int(count) for state, count in rows}
        return {
            "document_count": document_count,
            "cancelled": counts.get("cancelled", 0),
            "filtered": counts.get("filtered", 0),
            "awaiting_classification": counts.get("awaiting_classification", 0),
            "expired": counts.get("expired", 0),
            "awaiting_timing": counts.get("awaiting_timing", 0),
            "awaiting_completion": counts.get("awaiting_completion", 0),
            "rate_limited": counts.get("rate_limited", 0),
            "waiting_retry": counts.get("waiting_retry", 0),
            "youtube_requests": self.youtube_requests.snapshot(),
            "model_limits": self.model_gate.snapshot(time.time()),
            "current": current,
            "events": events,
            "console": console,
            "queued": counts.get("queued", 0),
            "acquiring": counts.get("acquiring", 0),
            "generating": counts.get("generating", 0),
            "ready": counts.get("ready", 0),
            "unavailable": counts.get("unavailable", 0),
            "failed": counts.get("failed", 0),
            "drain_paused": self.meta("drain_paused") == "1",
            "volume": {
                "transcript_characters": int(transcript_characters),
                "manuscript_characters": int(manuscript_characters),
            },
            # Provider pricing is not stable enough to present an invented estimate.
            "cost_estimate": None,
            "failures": [
                {
                    "video_id": str(row[0]),
                    "title": str(row[1]),
                    "state": str(row[2]),
                    "reason": row[3],
                }
                for row in failure_rows
            ],
            "batch": {
                "limit": int(self.meta("batch_limit") or 100),
                "completed": int(self.meta("batch_completed") or 0),
            },
        }

    def set_batch_progress(self, *, limit: int, completed: int) -> None:
        self.set_meta("batch_limit", str(limit))
        self.set_meta("batch_completed", str(completed))

    def set_meta(self, key: str, value: str) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO workspace_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def meta(self, key: str) -> str | None:
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT value FROM workspace_meta WHERE key = ?", (key,)
            ).fetchone()
        return str(row[0]) if row is not None else None

    def _asset_directory(self, video_id: str) -> Path:
        return self.root / "assets" / hashlib.sha256(video_id.encode("utf-8")).hexdigest()

    def replace_subscription_sources(self, sources: Iterable[SubscriptionSource]) -> None:
        sources = list(sources)  # Complete upstream pagination before touching membership.
        incoming = {source.channel_id for source in sources}
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = {row[0] for row in connection.execute("SELECT channel_id FROM subscription_sources")}
            removed = previous - incoming
            connection.executemany("INSERT OR IGNORE INTO unsubscribed_channels VALUES (?)", [(key,) for key in removed])
            connection.executemany("DELETE FROM unsubscribed_channels WHERE channel_id = ?", [(key,) for key in incoming])
            connection.executemany(
                "UPDATE candidates SET preparation_state = 'cancelled', failure_reason = ? "
                "WHERE channel_id = ? AND manuscript_version IS NULL AND request_kind != 'manual' "
                "AND preparation_state IN ('queued', 'rate_limited', 'waiting_retry', 'awaiting_classification', 'expired', 'awaiting_timing', 'awaiting_completion')",
                [("频道已取关，停止自动准备。已有文档保留。", key) for key in removed],
            )
            connection.execute("DELETE FROM subscription_sources")
            connection.executemany(
                """
                INSERT OR REPLACE INTO subscription_sources
                    (channel_id, title, description, thumbnail_url, subscribed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        source.channel_id,
                        source.title,
                        source.description,
                        source.thumbnail_url,
                        source.subscribed_at,
                    )
                    for source in sources
                ],
            )

    def observe_uploads(self, video_ids: list[str], started: float, finished: float) -> dict[str, dict[str, Any]]:
        """Retain first listing evidence even when details or admission later fail."""
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.executemany(
                'INSERT OR IGNORE INTO youtube_source_observations VALUES (?,?,?)',
                [(video_id, started, finished) for video_id in video_ids],
            )
            return {video_id: dict(connection.execute(
                'SELECT observed_from, observed_until FROM youtube_source_observations WHERE video_id=?',
                (video_id,),
            ).fetchone()) for video_id in video_ids}

    def source_scan(self, channel_id: str) -> dict[str, Any]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute('SELECT * FROM youtube_discovery_sources WHERE channel_id=?', (channel_id,)).fetchone()
        return dict(row) if row else {}

    def record_source_scan(self, channel_id: str, **values: object) -> None:
        allowed = {'uploads_playlist_id', 'uploads_checked_at', 'last_started_at', 'last_finished_at',
                   'last_success_at', 'valid_from', 'valid_until', 'complete', 'error', 'coverage_reason'}
        if not values or not set(values) <= allowed:
            raise ValueError('Invalid source scan facts')
        with sqlite3.connect(self.database_path) as connection:
            connection.execute('INSERT OR IGNORE INTO youtube_discovery_sources(channel_id) VALUES (?)', (channel_id,))
            connection.execute('UPDATE youtube_discovery_sources SET ' + ','.join(name+'=?' for name in values) + ' WHERE channel_id=?',
                               (*values.values(), channel_id))

    def source_scans(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(
                'SELECT s.title, d.* FROM youtube_discovery_sources d JOIN subscription_sources s USING(channel_id) ORDER BY s.title')]

    def subscription_source_count(self) -> int:
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute("SELECT COUNT(*) FROM subscription_sources").fetchone()
        return int(row[0]) if row is not None else 0

    def subscription_sources(self) -> list[dict[str, str | None]]:
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT channel_id, title, description, thumbnail_url, subscribed_at
                FROM subscription_sources
                ORDER BY title COLLATE NOCASE
                """
            ).fetchall()
        return [
            {
                "channel_id": str(row[0]),
                "title": str(row[1]),
                "description": str(row[2]),
                "thumbnail_url": row[3],
                "subscribed_at": row[4],
            }
            for row in rows
        ]

    def _initialize_database(self) -> None:
        with sqlite3.connect(self.database_path) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_meta'").fetchone():
                recorded = _recorded_contract(connection)
                if recorded > YOUTUBE_DATA_CONTRACT:
                    # Inspect the contract before any old migration can rewrite
                    # a library whose newer semantics this program cannot own.
                    self.newer_data_contract = (recorded, YOUTUBE_DATA_CONTRACT)
                    return
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(acceptance.SCHEMA)
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS youtube_source_observations (
                    video_id TEXT PRIMARY KEY, observed_from REAL NOT NULL, observed_until REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS youtube_discovery_sources (
                    channel_id TEXT PRIMARY KEY,
                    uploads_playlist_id TEXT, uploads_checked_at REAL,
                    last_started_at REAL, last_finished_at REAL, last_success_at REAL,
                    valid_from REAL, valid_until REAL, complete INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', coverage_reason TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS preparation_console (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
                    message TEXT NOT NULL,
                    occurred_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS preparation_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    occurred_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS excluded_channels (
                    channel_id TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS workspace_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS unsubscribed_channels (channel_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS subscription_sources (
                    channel_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    thumbnail_url TEXT,
                    subscribed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    video_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    channel_title TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    published_ts REAL,
                    discovered_at REAL NOT NULL,
                    preparation_state TEXT NOT NULL DEFAULT 'queued',
                    failure_reason TEXT,
                    reading_state TEXT NOT NULL DEFAULT 'inbox',
                    preparation_started_at REAL,
                    preparation_completed_at REAL,
                    manuscript_version INTEGER,
                    manuscript_path TEXT
                );
                CREATE TABLE IF NOT EXISTS manual_video_requests (request_id TEXT PRIMARY KEY, video_id TEXT NOT NULL, accepted_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS transcripts (
                    video_id TEXT PRIMARY KEY REFERENCES candidates(video_id) ON DELETE CASCADE,
                    language TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manuscripts (
                    video_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
                    version INTEGER NOT NULL, path TEXT NOT NULL, markdown TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY (video_id, version)
                );
                CREATE TABLE IF NOT EXISTS generation_records (
                    video_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
                    manuscript_version INTEGER NOT NULL,
                    generator TEXT NOT NULL,
                    transcript_characters INTEGER NOT NULL,
                    manuscript_characters INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (video_id, manuscript_version)
                );
                CREATE INDEX IF NOT EXISTS preparation_events_video ON preparation_events(video_id, occurred_at);
                CREATE INDEX IF NOT EXISTS candidates_preparation_state ON candidates(preparation_state);
                CREATE INDEX IF NOT EXISTS candidates_reading_state ON candidates(reading_state);
                """
            )
            existing = {row[1] for row in connection.execute("PRAGMA table_info(candidates)")}
            for name, definition in {
                "duration_seconds": "REAL",
                "preparation_stage": "TEXT",
                "stage_updated_at": "REAL",
                "reading_state": "TEXT NOT NULL DEFAULT 'inbox'",
                "preparation_started_at": "REAL",
                "preparation_completed_at": "REAL",
                "manuscript_version": "INTEGER",
                "manuscript_path": "TEXT",
                "shorts_status": "TEXT NOT NULL DEFAULT 'unknown'",
                "shorts_checked_at": "REAL",
                "shorts_retry_at": "REAL",
                # Retained column name for the model transient-error budget.
                # Shared acquisition recovery never spends it.
                "rate_limit_attempts": "INTEGER NOT NULL DEFAULT 0",
                "retry_at": "REAL",
                "acquisition_failure_kind": "TEXT",
                "caption_first_absent_at": "REAL",
                "caption_checks": "INTEGER NOT NULL DEFAULT 0",
                "caption_checks_ended_at": "REAL",
                "commenced_at": "REAL",
                "timing_status": "TEXT NOT NULL DEFAULT 'publication'",
                "completion_status": "TEXT NOT NULL DEFAULT 'unverified'",
                "actual_start_at": "TEXT",
                "actual_end_at": "TEXT",
                "scheduled_start_at": "TEXT",
                "timing_checked_at": "REAL",
                "playlist_added_at": "TEXT",
                "source_observed_at": "REAL",
                "source_observed_from": "REAL",
                "source_observed_until": "REAL",
                "request_kind": "TEXT NOT NULL DEFAULT 'automatic'",
                "summary": "TEXT CHECK(summary IS NULL OR length(summary) BETWEEN 1 AND 120)",
            }.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE candidates ADD COLUMN {name} {definition}")
            connection.execute("INSERT OR IGNORE INTO workspace_meta(key, value) VALUES ('drain_paused', '1')")
            if not connection.execute("SELECT 1 FROM workspace_meta WHERE key = 'youtube_commencement_v1'").fetchone():
                # Older cached format checks did not establish live/premiere timing.
                connection.execute(
                    "UPDATE candidates SET shorts_status = 'unknown' WHERE manuscript_version IS NULL AND preparation_state != 'filtered'"
                )
                for video_id, created_at, language, path, content in connection.execute(
                    """SELECT t.video_id, t.created_at, t.language, t.path, t.content FROM transcripts t
                       JOIN candidates c ON c.video_id = t.video_id
                       WHERE c.preparation_state NOT IN ('ready', 'cancelled', 'filtered')
                         AND c.manuscript_version IS NULL"""
                ).fetchall():
                    if (isinstance(created_at, (int, float)) and math.isfinite(created_at)
                            and 0 < created_at <= time.time()
                            and self._usable_transcript(video_id, language, path, content)):
                        connection.execute('UPDATE candidates SET commenced_at = COALESCE(commenced_at, ?) WHERE video_id = ?', (created_at, video_id))
                self._recheck_waiting(connection)
                connection.execute("INSERT INTO workspace_meta(key, value) VALUES ('youtube_commencement_v1', '1')")
            if not connection.execute(
                "SELECT 1 FROM workspace_meta WHERE key = 'youtube_rate_limit_migration_v1'"
            ).fetchone():
                # Recover only legacy subtitle throttles, never cancellations or genuinely absent captions.
                connection.execute(
                    """UPDATE candidates SET preparation_state = 'rate_limited',
                       preparation_stage = 'rate_limited', rate_limit_attempts = 0,
                       preparation_completed_at = NULL, stage_updated_at = ?
                       WHERE preparation_state = 'unavailable'
                       AND failure_reason LIKE '%download video subtitles%'
                       AND (failure_reason LIKE '%HTTP Error 429%' OR failure_reason LIKE '%Too Many Requests%')""",
                    (time.time(),),
                )
                connection.execute(
                    "INSERT INTO workspace_meta(key, value) VALUES ('youtube_rate_limit_migration_v1', '1')"
                )
            recorded = _recorded_contract(connection)
            if recorded < YOUTUBE_DATA_CONTRACT:
                connection.execute(
                    "INSERT INTO workspace_meta(key, value) VALUES ('youtube_data_contract', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(YOUTUBE_DATA_CONTRACT),))
            self.newer_data_contract = (recorded, YOUTUBE_DATA_CONTRACT) if recorded > YOUTUBE_DATA_CONTRACT else None


def _recorded_contract(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT value FROM workspace_meta WHERE key = 'youtube_data_contract'").fetchone()
    return int(row[0]) if row and str(row[0]).isdigit() else 0


def workspace_root() -> Path:
    configured = os.environ.get("YOUTUBE_WORKBENCH_WORKSPACE")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(user_data_dir("youtube-reading-workbench")) / "workspace"
