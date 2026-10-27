"""Explicit, private replay of the existing preparation pipeline; no host startup."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from platformdirs import user_data_dir

from coworker.secrets import state_dir
from coworker.server.youtube import HostManuscripts
from youtube_strataread.downloader import cues_to_lines, load_cues
from youtube_strataread.downloader.request_policy import YouTubeError
from youtube_strataread.downloader.youtube import SubtitleResult
from youtube_strataread.workbench.discovery import Candidate
from youtube_strataread.workbench.experiment_provider import (
    QuotaContract,
    classify_experiment_model,
)
from youtube_strataread.workbench.experiment_review import ConditionalReviewPipeline, ReviewPolicy
from youtube_strataread.workbench.experiment_source import UNAVAILABLE_REASON, normalize_captions
from youtube_strataread.workbench.experiment_translation import (
    CompletionCapacity,
    DirectTranslationPipeline,
    OutputSegmentation,
)
from youtube_strataread.workbench.library import PreparationService
from youtube_strataread.workbench.translation import (
    DEFAULTS,
    UPSTREAM_COMMIT,
    TranslationPipeline,
    settings,
    token_bound,
    validate_settings,
)
from youtube_strataread.workbench.workspace import LocalWorkspace

BASE_COMMIT = "dccf993"
VARIANT = "A-current-baseline"


@dataclass(frozen=True)
class CaptionSnapshot:
    path: Path
    video_id: str
    title: str
    language: str
    duration_seconds: float | None = None
    source_type: str = "speech"
    verified_rolling_pairs: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class ModelIdentity:
    id: str
    proxy_version: str
    configuration_sha256: str
    simulated: bool = False


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _repository() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]

    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    return {
        "url": "https://github.com/v6582374-netizen/Edison",
        "base_commit": git("rev-parse", BASE_COMMIT),
        "implementation_commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
    }


class _SnapshotCaptions:
    def __init__(self, snapshot: CaptionSnapshot, text: str):
        self.snapshot, self.text = snapshot, text

    def acquire(self, _url: str) -> SubtitleResult:
        return SubtitleResult(
            self.snapshot.video_id,
            self.snapshot.title,
            self.snapshot.language,
            False,
            self.text,
            duration_seconds=self.snapshot.duration_seconds,
        )


class _SnapshotVideo:
    def classify(self, _video_id: str) -> bool:
        # Offline snapshots are explicitly selected speech-caption samples.
        return False


class _RecordedCompletion:
    def __init__(
        self, complete: Callable[..., Any], directory: Path, stage: Callable[[], str] | None = None
    ):
        self.complete, self.directory = complete, directory
        self.stage = stage
        self.requests: list[dict[str, Any]] = []

    def __call__(self, model: str, messages: list, **options: Any) -> Any:
        request = {
            "attempt": len(self.requests) + 1,
            "model": model,
            "messages": copy.deepcopy(messages),
            "options": options,
            "stage": self.stage() if self.stage else "unknown",
            "estimated_input_token_bound": token_bound(json.dumps(messages, ensure_ascii=False)),
        }
        self.requests.append(request)
        path = self.directory / f"request-{len(self.requests):04d}.json"
        _write_json(path, request)
        started = time.monotonic()
        try:
            turn = self.complete(model, messages, **options)
        except Exception as error:
            request["error_type"] = type(error).__name__
            raise
        else:
            usage = getattr(turn, "usage", None)
            request["usage"] = usage.as_dict() if usage and any(usage.as_dict().values()) else None
            request["finish_reason"] = getattr(turn, "finish_reason", None)
            request["response"] = turn.text
            return turn
        finally:
            request["elapsed_seconds"] = time.monotonic() - started
            _write_json(path, request)

    def usage(self) -> dict[str, Any]:
        reported = [r["usage"] for r in self.requests if r.get("usage") is not None]
        return {
            "reported": {
                k: sum(r[k] for r in reported)
                for k in ("input", "output", "cache_read", "cache_write")
            }
            if reported
            else None,
            "requests_with_reported_usage": len(reported),
            "requests_without_reported_usage": len(self.requests) - len(reported),
            "basis": "provider-reported token fields; zero/absent usage is unknown; failed requests may still be billed",
            "cost": None,
            "estimated_input_token_bound": sum(
                r["estimated_input_token_bound"] for r in self.requests
            ),
            "estimate_basis": "UTF-8 bytes plus 32 per request, a conservative size bound, not measured consumption",
            "stage_request_seconds": {
                stage: sum(
                    r.get("elapsed_seconds", 0) for r in self.requests if r["stage"] == stage
                )
                for stage in sorted({r["stage"] for r in self.requests})
            },
        }


def _production_roots() -> set[Path]:
    roots = {
        state_dir(),
        Path.home() / ".config" / "coworker",
        Path(user_data_dir("youtube-reading-workbench")) / "workspace",
    }
    if configured := os.environ.get("YOUTUBE_WORKBENCH_WORKSPACE"):
        roots.add(Path(configured).expanduser())
    return {path.resolve() for path in roots}


def _checked_root(root: Path, source: Path) -> Path:
    root = root.expanduser().absolute()
    resolved = root.resolve()
    for production in _production_roots():
        if (
            resolved == production
            or resolved.is_relative_to(production)
            or production.is_relative_to(resolved)
        ):
            raise ValueError("Experiment path overlaps production storage")
        if source.resolve().is_relative_to(production):
            raise ValueError("Copy the source snapshot outside production storage before replay")
    if source.resolve().is_relative_to(resolved):
        raise ValueError("Source snapshot must be outside writable experiment storage")
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise ValueError("Experiment path cannot use symlinks")
    if root.exists():
        for path in root.rglob("*"):
            if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
                raise ValueError("Experiment storage cannot contain symlinks or hardlinks")
    return resolved


def run_baseline(
    root: Path,
    snapshot: CaptionSnapshot,
    model: ModelIdentity,
    complete: Callable[..., Any],
    *,
    config: dict[str, Any] | None = None,
    historical_diagnostic: bool = False,
    quota_contract: QuotaContract | None = None,
    source_policy: str = "conservative-v1",
    variant: str = "A-current-baseline",
    capacity: CompletionCapacity | None = None,
    segmentation: OutputSegmentation | None = None,
    review_policy: ReviewPolicy | None = None,
) -> Path:
    """Run exactly one explicit preparation attempt and return its private record."""
    if quota_contract and quota_contract.configuration_sha256 != model.configuration_sha256:
        raise ValueError("Quota contract does not match the configured proxy identity")
    if source_policy not in {"conservative-v1", "historical"}:
        raise ValueError("Unknown source normalization policy")

    if variant not in {"A-current-baseline", "B-direct", "C-conditional"}:
        raise ValueError("Unknown experiment variant")
    if capacity is not None:
        capacity.validate(simulated_model=model.simulated, configuration_sha256=model.configuration_sha256, model_id=model.id)
    if variant != "A-current-baseline" and (source_policy != "conservative-v1" or capacity is None):
        raise ValueError("Direct variants require conservative source and an explicit capacity contract")
    segmentation = segmentation or OutputSegmentation()
    config = validate_settings(config if config is not None else DEFAULTS)
    root = _checked_root(root, snapshot.path)
    repository = _repository()
    source = snapshot.path.read_bytes()
    text = source.decode("utf-8")
    source_identity = {
        "sha256": hashlib.sha256(source).hexdigest(),
        "video_id": snapshot.video_id,
        "title": snapshot.title,
        "language": snapshot.language,
        "duration_seconds": snapshot.duration_seconds,
        "bytes": len(source),
        "source_type": snapshot.source_type,
        "verified_rolling_pairs": [list(pair) for pair in snapshot.verified_rolling_pairs],
    }
    marker = {
        "format": "edison-translation-experiment",
        "schema_version": 1,
        "source": source_identity,
    }
    marker_path = root / "experiment.json"
    if (
        root.exists()
        and any(root.iterdir())
        and (not marker_path.is_file() or json.loads(marker_path.read_text()) != marker)
    ):
        raise ValueError("Experiment root is not owned by this snapshot; select a new empty path")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    if not marker_path.exists():
        _write_json(marker_path, marker)
    directory = root / "runs" / uuid.uuid4().hex
    directory.mkdir(mode=0o700, parents=True)
    (directory / "source.srt").write_bytes(source)
    (directory / "source.srt").chmod(0o400)
    workspace = LocalWorkspace.open(root / "workspace")
    # Seed settings: never read/migrate global user prompts from an experiment.
    workspace.set_meta("youtube_translation", json.dumps(config, ensure_ascii=False))
    config = settings(workspace)
    # Synthetic offline admission date, never evidence of actual video publication.
    workspace.add_candidate(
        Candidate(
            snapshot.video_id,
            "experiment",
            "Private snapshot",
            snapshot.title,
            f"https://www.youtube.com/watch?v={snapshot.video_id}",
            "1970-01-01T00:00:00+00:00",
            0,
        )
    )
    workspace.queue_regeneration(snapshot.video_id)
    recorder = _RecordedCompletion(
        complete,
        directory,
        lambda: next(
            (
                str(item["preparation_stage"])
                for item in workspace.activity()["current"]
                if item["video_id"] == snapshot.video_id
            ),
            "unknown",
        ),
    )
    manager = SimpleNamespace(model=model.id, provider_complete=recorder)
    normalized = None

    def normalize(text: str, language: str) -> str:
        nonlocal normalized
        if snapshot.source_type != "speech" or language == "live_chat" or not text.strip():
            raise YouTubeError(UNAVAILABLE_REASON)
        if source_policy == "historical":
            cleaned = "\n".join(cues_to_lines(load_cues(text))).strip()
            if not cleaned:
                raise YouTubeError(UNAVAILABLE_REASON)
            return cleaned
        normalized = normalize_captions(
            text,
            source_type=snapshot.source_type,
            verified_rolling_pairs=snapshot.verified_rolling_pairs,
        )
        return normalized.text

    candidate_pipeline = None

    def direct_factory(*args, **kwargs):
        nonlocal candidate_pipeline
        factory = ConditionalReviewPipeline if variant == "C-conditional" else DirectTranslationPipeline
        if variant == "C-conditional":
            kwargs["review_policy"] = review_policy
            kwargs["on_review"] = lambda events: _write_json(directory / "conditional-review.json", events)
        candidate_pipeline = factory(*args, source_units=normalized.units, capacity=capacity, segmentation=segmentation, **kwargs)
        return candidate_pipeline

    preparation = PreparationService(
        workspace,
        _SnapshotCaptions(snapshot, text),
        HostManuscripts(
            manager,
            workspace,
            pipeline_factory=direct_factory if variant != "A-current-baseline" else TranslationPipeline,
            pipeline_options={
                "enforce_local_budgets": historical_diagnostic,
                "output_tokens": capacity.output_tokens if capacity else 8192,
                "quota_exhausted": quota_contract.recognizes if quota_contract else None,
            },
        ),
        shorts=_SnapshotVideo(),
        model_failure_classifier=classify_experiment_model,
        transcript_normalizer=normalize,
    )
    started = time.monotonic()
    preparation.run_next()
    asset = workspace.asset(snapshot.video_id)
    artifacts = {"raw_captions": "source.srt"}
    if candidate_pipeline is not None and variant == "C-conditional":
        _write_json(directory / "conditional-review.json", candidate_pipeline.review_events)
        artifacts["conditional_review"] = "conditional-review.json"
    if normalized is not None:
        _write_json(directory / "normalized-source.json", normalized.record())
        (directory / "normalized-source.json").chmod(0o400)
        artifacts["normalized_source"] = "normalized-source.json"
    if asset["preparation_state"] == "ready":
        document = workspace.document(snapshot.video_id)
        for name, path in [
            ("translation", document["translation_path"]),
            ("manuscript", document["path"]),
        ]:
            filename = f"{name}.md"
            (directory / filename).write_bytes(Path(path).read_bytes())
            artifacts[name] = filename
        provenance = Path(document["path"]).with_name("translation-run.json")
        (directory / "baseline-provenance.json").write_bytes(provenance.read_bytes())
        artifacts["baseline_provenance"] = "baseline-provenance.json"
        if variant != "A-current-baseline":
            artifacts["candidate_provenance"] = "baseline-provenance.json"
        artifacts["workspace_manuscript"] = os.path.relpath(document["path"], directory)
        artifacts["manuscript_version"] = document["version"]
    record = {
        "schema_version": 1,
        "run_id": directory.name,
        "variant": variant,
        "segmentation": asdict(segmentation) if variant != "A-current-baseline" else {"strategy": "upstream-current"},
        "comparison_settings": {
            "country": config["country"], "temperature": 0.3,
            "requested_output_tokens": capacity.output_tokens if capacity else 8192,
            "source_policy": source_policy,
            "consumption_policy": "historical-local-budget" if historical_diagnostic else "no-local-cumulative-budget",
        },
        "capacity": asdict(capacity) if capacity else None,
        "stage_elapsed_seconds": candidate_pipeline.stage_elapsed_seconds if candidate_pipeline else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": repository,
        "upstream": {
            "repository": "https://github.com/andrewyng/translation-agent",
            "commit": UPSTREAM_COMMIT,
        },
        "model": asdict(model),
        "source": source_identity,
        "source_processing": {
            "policy": source_policy,
            "normalized_sha256": normalized.record()["sha256"] if normalized else None,
        },
        "settings": config,
        "state": asset["preparation_state"],
        "reason": asset["failure_reason"],
        "attempts": len(recorder.requests),
        "quota_contract": asdict(quota_contract) if quota_contract else {"verified": False},
        "usage": recorder.usage(),
        "elapsed_seconds": time.monotonic() - started,
        "artifacts": artifacts,
        "fair_long_comparison": False,
        "consumption_policy": "historical-local-budget"
        if historical_diagnostic
        else "no-local-cumulative-budget",
        "limitations": [
            "Main comparison disables local cumulative budgets; historical_diagnostic explicitly retains them. Source normalization, provider and sample evidence must also match before a fair long comparison.",
            "Simulated responses establish plumbing only, not translation quality or effective provider capacity.",
        ],
    }
    path = directory / "run.json"
    _write_json(path, record)
    return path


def main(argv: list[str] | None = None) -> int:
    """Offline smoke entry: supplied responses only, never a live provider."""
    import argparse

    from coworker.providers.base import AssistantTurn, TokenUsage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--title", default="Private experiment snapshot")
    parser.add_argument("--language", required=True)
    parser.add_argument(
        "--source-type", choices=["speech", "live_chat", "absent"], default="speech"
    )
    parser.add_argument(
        "--source-policy", choices=["conservative-v1", "historical"], default="conservative-v1"
    )
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument(
        "--historical-diagnostic",
        action="store_true",
        help="Retain historical local cumulative budgets for a separate diagnostic",
    )
    parser.add_argument(
        "--mock-responses",
        type=Path,
        required=True,
        help="Private JSON array of response objects, consumed once in order",
    )
    parser.add_argument("--variant", choices=("A-current-baseline", "B-direct", "C-conditional"), default="A-current-baseline")
    parser.add_argument("--output-strategy", choices=("whole", "semantic-chapters"), default="whole")
    parser.add_argument("--chapter-end-id", action="append", default=[])
    parser.add_argument("--chapter-target-characters", type=int, default=12000)
    parser.add_argument("--capacity-input-characters", type=int)
    parser.add_argument("--output-tokens", type=int, default=8192)
    args = parser.parse_args(argv)
    if args.variant != "A-current-baseline" and args.capacity_input_characters is None:
        parser.error("Direct mock runs require --capacity-input-characters")
    payload = args.mock_responses.read_bytes()
    responses = iter(json.loads(payload))

    def complete(_model: str, _messages: list, **_options: Any) -> AssistantTurn:
        try:
            value = next(responses)
        except StopIteration as error:
            raise ValueError("Mock response fixture exhausted") from error
        usage = value.get("usage")
        return AssistantTurn(
            text=value["text"],
            finish_reason=value.get("finish_reason", "stop"),
            usage=TokenUsage(**usage) if usage else None,
        )

    path = run_baseline(
        args.root,
        CaptionSnapshot(
            args.snapshot,
            args.video_id,
            args.title,
            args.language,
            args.duration_seconds,
            args.source_type,
        ),
        ModelIdentity(
            "mock/fixture-replay", "fixture-replay-v1", hashlib.sha256(payload).hexdigest(), True
        ),
        complete,
        variant=args.variant,
        capacity=CompletionCapacity(args.capacity_input_characters, args.output_tokens, simulated=True) if args.capacity_input_characters else None,
        segmentation=OutputSegmentation(args.output_strategy, tuple(args.chapter_end_id), args.chapter_target_characters),
        historical_diagnostic=args.historical_diagnostic,
        source_policy=args.source_policy,
    )
    state = json.loads(path.read_text())["state"]
    print(json.dumps({"record": str(path), "state": state}))
    return 0 if state == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
