"""Private fixed samples and bounded, explicitly limited comparison evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import srt
from platformdirs import user_data_dir

from coworker.secrets import state_dir


@dataclass(frozen=True)
class Sample:
    id: str
    snapshot: Path
    video_id: str
    language: str
    source_kind: str
    split: str
    duration_seconds: float | None
    duration_evidence: str | None
    categories: tuple[str, ...] = ()
    used_for_tuning: bool = False


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _create_private(path: Path, value: Any) -> None:
    path = path.expanduser().absolute()
    protected = {
        state_dir().resolve(),
        (Path.home() / ".config/coworker").resolve(),
        (Path(user_data_dir("youtube-reading-workbench")) / "workspace").resolve(),
    }
    if configured := os.environ.get("YOUTUBE_WORKBENCH_WORKSPACE"):
        protected.add(Path(configured).expanduser().resolve())
    resolved = path.expanduser().resolve()
    if any(resolved.is_relative_to(root) for root in protected):
        raise ValueError("Evaluation output overlaps production storage")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Evaluation output cannot use symlinks")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def freeze_manifest(path: Path, samples: list[Sample]) -> dict[str, Any]:
    """Create once; input bytes and declared metadata define the fixed sample set."""
    rows = []
    ids = set()
    splits: dict[str, str] = {}
    for sample in samples:
        if not sample.id or sample.id in ids:
            raise ValueError("Sample identities must be nonempty and unique")
        ids.add(sample.id)
        if sample.split not in {"development", "held-out"}:
            raise ValueError("Sample split must be development or held-out")
        if sample.source_kind not in {"speech-captions", "synthetic", "chat", "absent"}:
            raise ValueError("Unknown source kind")
        if sample.split == "held-out" and sample.used_for_tuning:
            raise ValueError("A tuned sample cannot be independent held-out evidence")
        if sample.duration_seconds is not None and (
            isinstance(sample.duration_seconds, bool)
            or not math.isfinite(sample.duration_seconds)
            or sample.duration_seconds <= 0
            or not sample.duration_evidence
        ):
            raise ValueError("Known duration needs a positive measurement and evidence")
        raw = sample.snapshot.read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        for identity in (source_hash, sample.video_id):
            if identity in splits and splits[identity] != sample.split:
                raise ValueError("A source cannot occur in both development and held-out sets")
            splits[identity] = sample.split
        row = asdict(sample)
        row.update(
            snapshot=str(sample.snapshot.resolve()),
            sha256=source_hash,
            bytes=len(raw),
            characters=len(raw.decode("utf-8")),
            cue_count=len(list(srt.parse(raw.decode("utf-8")))),
        )
        rows.append(row)
    manifest = {"schema_version": 1, "samples": rows}
    manifest["identity_sha256"] = _digest(manifest)
    _create_private(path, manifest)
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    """Verify the frozen metadata and every raw source before another run."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = {key: value for key, value in manifest.items() if key != "identity_sha256"}
    if manifest.get("schema_version") != 1 or manifest.get("identity_sha256") != _digest(expected):
        raise ValueError("Manifest identity hash changed")
    for sample in manifest["samples"]:
        if hashlib.sha256(Path(sample["snapshot"]).read_bytes()).hexdigest() != sample["sha256"]:
            raise ValueError(f"Source hash changed for sample {sample['id']}")
    return manifest


def sample_gaps(manifest: dict[str, Any]) -> list[str]:
    """Report missing real categories; timestamps never stand in for duration."""
    speech = [row for row in manifest["samples"] if row["source_kind"] == "speech-captions"]
    covered = {
        category
        for row in speech
        for category in row["categories"]
        if category in {"short-english", "chinese-or-mixed", "multi-speaker", "number-dense"}
    }
    if any(
        row["duration_seconds"] is not None and 6300 <= row["duration_seconds"] <= 8100
        for row in speech
    ):
        covered.add("approximately-2h-speech")
    if any(
        row["duration_seconds"] is not None and 12600 <= row["duration_seconds"] <= 16200
        for row in speech
    ):
        covered.add("approximately-4h-speech")
    if any(row["split"] == "held-out" for row in speech):
        covered.add("held-out-speech")
    required = {
        "approximately-2h-speech",
        "approximately-4h-speech",
        "short-english",
        "chinese-or-mixed",
        "multi-speaker",
        "number-dense",
        "held-out-speech",
    }
    return sorted(required - covered)


def build_plan(
    path: Path,
    manifest: dict[str, Any],
    *,
    model_identity: dict[str, Any],
    settings_sha256: str,
    representative_ids: tuple[str, ...] = (),
    repeats: int = 2,
    segmentation: str = "fixed-common-source-units",
) -> dict[str, Any]:
    """Freeze A/B/C order once; segmentation belongs in a separate plan."""
    if type(repeats) is not int or not 1 <= repeats <= 3:
        raise ValueError("Representative repeats must be between 1 and 3")
    samples = {sample["id"]: sample for sample in manifest["samples"]}
    if set(representative_ids) - samples.keys() or len(set(representative_ids)) != len(
        representative_ids
    ):
        raise ValueError("Representative identities must refer to distinct fixed samples")
    runs = []
    rounds = [(sample, 1) for sample in samples.values()]
    rounds.extend(
        (samples[id], repeat) for repeat in range(2, repeats + 1) for id in representative_ids
    )
    for group, (sample, repeat) in enumerate(rounds):
        variants = ("A", "B", "C")
        rotation = group % len(variants)
        for variant in variants[rotation:] + variants[:rotation]:
            runs.append(
                {
                    "order": len(runs) + 1,
                    "sample_id": sample["id"],
                    "split": sample["split"],
                    "source_sha256": sample["sha256"],
                    "variant": variant,
                    "repetition": repeat,
                }
            )
    plan = {
        "schema_version": 1,
        "manifest_sha256": manifest["identity_sha256"],
        "model": model_identity,
        "settings_sha256": settings_sha256,
        "changed_factor": "translation mechanism",
        "segmentation": segmentation,
        "runs": runs,
        "sample_gaps": sample_gaps(manifest),
        "held_out_policy": "Do not tune on held-out outputs; after tuning, relabel as development and freeze a new plan.",
    }
    plan["identity_sha256"] = _digest(plan)
    _create_private(path, plan)
    return plan


def load_plan(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Refuse a changed order or a plan for another frozen source set."""
    plan = json.loads(path.read_text(encoding="utf-8"))
    expected = {key: value for key, value in plan.items() if key != "identity_sha256"}
    if (
        plan.get("identity_sha256") != _digest(expected)
        or plan.get("manifest_sha256") != manifest["identity_sha256"]
    ):
        raise ValueError("Plan identity hash or fixed manifest changed")
    return plan


DIMENSIONS = (
    "omission",
    "addition",
    "number",
    "unit",
    "negation",
    "qualification",
    "terminology",
    "entity",
    "order",
    "readability",
)


@dataclass(frozen=True)
class ExpectedCheck:
    id: str
    dimension: str
    source_cues: tuple[int, int]
    alternatives: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()


def _artifact(record_path: Path, record: dict[str, Any], name: str) -> str | None:
    relative = record.get("artifacts", {}).get(name)
    if relative is None:
        return None
    path = (record_path.parent / relative).resolve()
    if not path.is_relative_to(record_path.parent.resolve()):
        raise ValueError("Evaluation artifact must be inside its private run directory")
    return path.read_bytes().decode("utf-8")


def evaluate_run(
    record_path: Path,
    checks: list[ExpectedCheck],
    *,
    source_kind: str,
) -> dict[str, Any]:
    """Check annotated expectations in both artifacts, not semantic correctness."""
    record = json.loads(record_path.read_text(encoding="utf-8"))
    raw = _artifact(record_path, record, "raw_captions")
    if raw is None or hashlib.sha256(raw.encode()).hexdigest() != record["source"]["sha256"]:
        raise ValueError("Run source identity does not match raw captions")
    cue_count = len(list(srt.parse(raw)))
    seen = set()
    for check in checks:
        start, end = check.source_cues
        if (
            not check.id
            or check.id in seen
            or check.dimension not in DIMENSIONS
            or not 1 <= start <= end <= cue_count
            or not (check.alternatives or check.forbidden)
            or any(not value for value in (*check.alternatives, *check.forbidden))
        ):
            raise ValueError(
                "Each expectation needs a distinct id, dimension and valid source cue range"
            )
        seen.add(check.id)
    outcomes = []
    for artifact in ("translation", "manuscript"):
        text = _artifact(record_path, record, artifact) if record["state"] == "ready" else None
        order = sorted(
            (check for check in checks if check.dimension == "order"),
            key=lambda check: check.source_cues,
        )
        positions = [
            min((text.find(value) for value in check.alternatives if value in text), default=-1)
            if text is not None
            else -1
            for check in order
        ]
        invalid_order = any(
            left >= right for left, right in zip(positions, positions[1:], strict=False)
        )
        for check in checks:
            if text is None:
                status = "unknown"
            else:
                present = not check.alternatives or any(
                    value in text for value in check.alternatives
                )
                forbidden = any(value in text for value in check.forbidden)
                status = "pass" if present and not forbidden else "fail"
                if check.dimension == "order" and invalid_order:
                    status = "fail"
            outcomes.append(
                {
                    "check_id": check.id,
                    "dimension": check.dimension,
                    "artifact": artifact,
                    "source_cues": list(check.source_cues),
                    "status": status,
                }
            )
    dimensions = {}
    for dimension in DIMENSIONS:
        states = [row["status"] for row in outcomes if row["dimension"] == dimension]
        dimensions[dimension] = (
            "fail"
            if "fail" in states
            else ("pass" if states and all(state == "pass" for state in states) else "unknown")
        )
    return {
        "schema_version": 1,
        "run_id": record["run_id"],
        "source_sha256": record["source"]["sha256"],
        "source_kind": source_kind,
        "dimensions": dimensions
        if source_kind == "synthetic"
        else dict.fromkeys(DIMENSIONS, "unknown"),
        "annotated_dimensions": dimensions,
        "checks": outcomes,
        "failed_checks": [row for row in outcomes if row["status"] == "fail"],
        "method": {
            "id": "annotated-literal-checks-v1",
            "judge_model": None,
            "limitations": "Only explicitly annotated phrases and order are checked. Paraphrases may fail; unannotated additions, meaning and readability are unknown.",
        },
        "semantic_fidelity_certified": False,
        "real_sample_validation": "unverified"
        if source_kind != "synthetic"
        else "synthetic expectations only",
    }


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def evaluate_with_judge(
    record_path: Path,
    judge: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    model_identity: dict[str, Any],
    method_id: str,
    limitations: str,
) -> dict[str, Any]:
    """Inject a fallible external judge; require quotes tied to actual source cues."""
    if not model_identity.get("id") or not method_id.strip() or not limitations.strip():
        raise ValueError("A judge needs model identity, method and explicit limitations")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    raw = _artifact(record_path, record, "raw_captions")
    if raw is None or hashlib.sha256(raw.encode()).hexdigest() != record["source"]["sha256"]:
        raise ValueError("Run source identity does not match raw captions")
    cues = list(srt.parse(raw))
    artifacts = {key: _artifact(record_path, record, key) for key in ("translation", "manuscript")}
    evidence = {
        "source_cues": [
            {
                "ordinal": i + 1,
                "text": cue.content,
                "start_seconds": cue.start.total_seconds(),
                "end_seconds": cue.end.total_seconds(),
            }
            for i, cue in enumerate(cues)
        ],
        **artifacts,
        "dimensions": DIMENSIONS,
    }
    decisions = judge(evidence) if record["state"] == "ready" and all(artifacts.values()) else {}
    dimensions = dict.fromkeys(DIMENSIONS, "unknown")
    accepted = []
    for dimension, decision in decisions.items():
        if dimension not in DIMENSIONS or decision.get("status") not in {"pass", "fail", "unknown"}:
            raise ValueError("Judge returned an invalid dimension or status")
        if decision["status"] == "unknown":
            continue
        start, end = decision.get("source_cues", (0, 0))
        quote = decision.get("source_quote", "")
        output_quote = decision.get("output_quote", "")
        artifact = artifacts.get(decision.get("artifact"))
        if (
            not 1 <= start <= end <= len(cues)
            or not quote
            or not output_quote
            or quote not in "\n".join(cue.content for cue in cues[start - 1 : end])
            or artifact is None
            or output_quote not in artifact
        ):
            raise ValueError("Judge evidence is not aligned to the retained source and output")
        dimensions[dimension] = decision["status"]
        accepted.append({"dimension": dimension, **decision})
    return {
        "schema_version": 1,
        "run_id": record["run_id"],
        "source_sha256": record["source"]["sha256"],
        "dimensions": dimensions,
        "checks": accepted,
        "method": {"id": method_id, "judge_model": model_identity, "limitations": limitations},
        "semantic_fidelity_certified": False,
        "real_sample_validation": "automated judgement only; source alignment does not prove correctness",
    }


def summarize_runs(
    path: Path,
    record_paths: list[Path],
    *,
    evaluations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write scalar evidence without turning missing measurements into zeroes."""
    quality = {value["run_id"]: value for value in evaluations or []}
    rows = []
    seen = set()
    for record_path in record_paths:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record["run_id"] in seen:
            raise ValueError("Duplicate run identity cannot count as another observation")
        seen.add(record["run_id"])
        evaluation = quality.get(record["run_id"])
        if evaluation and evaluation["source_sha256"] != record["source"]["sha256"]:
            raise ValueError("Evaluation source does not match the run")
        usage = record.get("usage", {})
        rows.append(
            {
                "run_id": record["run_id"],
                "variant": record["variant"],
                "source_sha256": record["source"]["sha256"],
                "comparison_identity": _digest(
                    {
                        "source": record["source"]["sha256"],
                        "model": record["model"],
                        "settings": record.get("comparison_settings", record["settings"]),
                        "segmentation": record.get("segmentation"),
                        "effective_chapter_plan_sha256": record.get("effective_chapter_plan_sha256"),
                        "source_processing": record.get("source_processing"),
                    }
                ),
                "state": record["state"],
                "reason": record.get("reason"),
                "attempts": record["attempts"],
                "elapsed_seconds": record["elapsed_seconds"],
                "stage_elapsed_seconds": record.get("stage_elapsed_seconds"),
                "estimated_tokens": usage.get("estimated_tokens"),
                "reported_usage": usage.get("reported"),
                "requests_with_reported_usage": usage.get("requests_with_reported_usage"),
                "requests_without_reported_usage": usage.get("requests_without_reported_usage"),
                "cost": usage.get("cost"),
                "recovery": record.get("recovery"),
                "quality": evaluation["dimensions"] if evaluation else None,
                "evaluation_method": evaluation["method"] if evaluation else None,
            }
        )
    variants = {}
    for variant in sorted({row["variant"] for row in rows}):
        group = [row for row in rows if row["variant"] == variant]
        reported = [row["reported_usage"] for row in group if row["reported_usage"] is not None]
        latencies = [row["elapsed_seconds"] for row in group]
        variants[variant] = {
            "total": len(group),
            "ready": sum(row["state"] == "ready" for row in group),
            "attempts": sum(row["attempts"] for row in group),
            "elapsed_seconds": {
                "n": len(latencies),
                "p50": _quantile(latencies, 0.5),
                "p95": _quantile(latencies, 0.95),
            },
            "reported_usage": {
                key: sum(value.get(key, 0) for value in reported)
                for key in ("input", "output", "cache_read", "cache_write")
            }
            if reported
            else None,
            "runs_without_usage": len(group) - len(reported),
            "cost": sum(row["cost"] for row in group)
            if all(row["cost"] is not None for row in group)
            else None,
        }
    summary = {
        "schema_version": 1,
        "runs": rows,
        "variants": variants,
        "quantile_method": "Linear interpolation of observed run latencies; descriptive only, especially at small n, never a population guarantee.",
        "comparison_rule": "Only matching comparison_identity values are paired evidence; aggregates across unmatched sources/models/settings cannot establish mechanism improvement.",
        "semantic_fidelity_certified": False,
    }
    _create_private(path, summary)
    return summary
