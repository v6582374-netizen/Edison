"""Bounded conditional checks for the isolated direct translation candidate."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from youtube_strataread.workbench.experiment_translation import DirectTranslationPipeline
from youtube_strataread.workbench.translation import TranslationError

REVIEW_PROMPT = (
    "仅检查和必要时修复指定来源范围的译文，保留全部事实、数字、单位、否定、归属和顺序。"
    "不得修改其他范围，不摘要或扩写。只输出JSON对象：decision为accept或replace，"
    "units为source_ids/text对象数组，恰好覆盖affected_source_ids且保持顺序。"
    "结构缺失或截断必须replace；复核无误可accept并原样保留译文。"
    "数字、单位、否定词表只提供待核实信号，不是错误证明；等义表达可以accept。"
)


@dataclass(frozen=True)
class ReviewPolicy:
    max_repairs: int = 2

    def __post_init__(self):
        if type(self.max_repairs) is not int or not 1 <= self.max_repairs <= 3:
            raise ValueError("Conditional repair limit must be between one and three")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


_NUMERALS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_SCALES = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}


def _chinese_integer(text):
    if all(char in _NUMERALS for char in text):
        return str(int("".join(str(_NUMERALS[char]) for char in text)))
    total = section = digit = 0
    for char in text:
        if char in _NUMERALS:
            digit = _NUMERALS[char]
        elif _SCALES[char] < 10000:
            section += (digit or 1) * _SCALES[char]
            digit = 0
        else:
            total += (section + digit) * _SCALES[char]
            section = digit = 0
    return str(total + section + digit)


def _numbers(text):
    text = text.translate(str.maketrans({"兩": "两", "萬": "万", "億": "亿"})).replace("百分之", "")
    values = re.findall(
        r"(?<![A-Za-z0-9])\d+(?:[,.]\d+)*(?![\d])|[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]+)?",
        text,
    )
    normalized = []
    for value in values:
        if value[0] in _NUMERALS or value[0] in _SCALES:
            integer, separator, fraction = value.partition("点")
            value = _chinese_integer(integer)
            if separator:
                value += "." + "".join(str(_NUMERALS[char]) for char in fraction)
        normalized.append(format(Decimal(value.replace(",", "")).normalize(), "f"))
    return Counter(normalized)


def _signals(source, output):
    triggers = []
    numbers = _numbers(source)
    if numbers and numbers != _numbers(output):
        triggers.append("number")
    if sum(numbers.values()) > 1:
        triggers.append("number_association")
    units = [
        (r"\bdollars?\b|美元", r"\bdollars?\b|美元"),
        (r"\byears?\b|岁", r"\byears?\b|年|岁"),
        (r"\bpercent\b|%|百分之", r"\bpercent\b|%|百分之"),
        (r"\bkilograms?\b|千克|公斤", r"\bkilograms?\b|千克|公斤"),
    ]
    if any(
        re.search(pattern, source, re.I) and not re.search(translated, output, re.I)
        for pattern, translated in units
    ):
        triggers.append("unit")
    negative = r"\b(?:not|never|no|without|cannot)\b|n't|不|没|无|禁止"
    if re.search(negative, source, re.I) and not re.search(negative, output, re.I):
        triggers.append("negation")
    # Multiple amounts can preserve every numeral while swapping owners or units.
    # The automatic check is fallible; these signals do not certify semantics.
    return triggers


class ConditionalReviewPipeline(DirectTranslationPipeline):
    def __init__(self, *args, review_policy: ReviewPolicy | None = None, on_review=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.review_policy = review_policy or ReviewPolicy()
        self.on_review = on_review
        self.review_events: list[dict] = []
        self.repairs = 0
        self.review_attempts = 0
        self.identity.update(
            variant="C-conditional",
            review_policy=asdict(self.review_policy),
            review_prompt=REVIEW_PROMPT,
        )
        self.identity_sha256 = _hash(self.identity)
        self.last_response = None
        original = self.complete

        def observe(*args, **kwargs):
            turn = original(*args, **kwargs)
            self.last_response = turn
            return turn

        self.complete = observe

    def _event(self, **value):
        self.review_events.append(value)
        if self.on_review:
            self.on_review(self.review_events)

    @staticmethod
    def _safe_rows(text, expected):
        """Only nonconflicting, contiguous source ranges can survive a bad draft."""
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            return []
        if (
            not isinstance(value, dict)
            or set(value) != {"units"}
            or not isinstance(value["units"], list)
        ):
            return []
        positions = {id: index for index, id in enumerate(expected)}
        rows = []
        for row in value["units"]:
            if not isinstance(row, dict) or set(row) != {"source_ids", "text"}:
                continue
            ids = row["source_ids"]
            if (
                not isinstance(ids, list)
                or not ids
                or not all(isinstance(id, str) and id in positions for id in ids)
            ):
                continue
            start = positions[ids[0]]
            if (
                ids != expected[start : start + len(ids)]
                or not isinstance(row["text"], str)
                or not row["text"].strip()
            ):
                continue
            rows.append(row)
        counts = Counter(id for row in rows for id in row["source_ids"])
        return sorted(
            (row for row in rows if all(counts[id] == 1 for id in row["source_ids"])),
            key=lambda row: positions[row["source_ids"][0]],
        )

    def _translate_chapter(self, chapter, full_source):
        messages = self._chapter_messages(chapter, full_source)
        self.last_response = None
        incomplete = False
        try:
            draft = self._request(f"conditional:draft:{chapter.id}", messages, temperature=0.3)
        except TranslationError:
            if self.last_response is None:
                raise
            draft = self.last_response.text or ""
            incomplete = True
        rows = [] if incomplete else self._safe_rows(draft, chapter.source_ids)
        retained = {id for row in rows for id in row["source_ids"]}
        source_order_changed = False
        source_structure_invalid = False
        if rows and len(retained) == len(chapter.source_ids):
            original_rows = json.loads(draft)["units"]
            source_structure_invalid = len(rows) != len(original_rows)
            if not source_structure_invalid:
                original_ids = [id for row in original_rows for id in row["source_ids"]]
                source_order_changed = original_ids != chapter.source_ids
            if source_order_changed or source_structure_invalid:
                rows = []
                retained = set()
        affected = [id for id in chapter.source_ids if id not in retained]
        semantic_ids = []
        semantic_triggers = []
        for row in rows:
            row_source = "\n".join(
                unit["text"] for unit in full_source if unit["id"] in row["source_ids"]
            )
            signals = _signals(row_source, row["text"])
            if signals:
                semantic_ids.extend(row["source_ids"])
                semantic_triggers.extend(signals)
        structural = bool(affected)
        affected = [id for id in chapter.source_ids if id in set(affected + semantic_ids)]
        if not affected:
            # Restoring the ID order is deterministic; prose within each range is unchanged.
            self._parse_units(json.dumps({"units": rows}), chapter.source_ids)
            return rows
        triggers = sorted(
            set(
                (
                    [
                        "incomplete_response"
                        if incomplete
                        else "source_structure"
                        if source_structure_invalid
                        else "source_order"
                        if source_order_changed
                        else "source_coverage"
                    ]
                    if structural
                    else []
                )
                + semantic_triggers
            )
        )
        accepted = [row for row in rows if not any(id in affected for id in row["source_ids"])]
        current = [row for row in rows if any(id in affected for id in row["source_ids"])]
        self._event(
            chapter=chapter.id,
            triggers=triggers,
            affected_source_ids=affected,
            result="triggered",
            rejected_response=draft,
        )
        original_current = current
        previous = _hash(draft)
        for attempt in range(1, self.review_policy.max_repairs + 1):
            self.review_attempts += 1
            if self.on_progress:
                self.on_progress("review", f"定向复核第 {attempt} 次")
            payload = {
                "affected_source_ids": affected,
                "source": [unit for unit in full_source if unit["id"] in affected],
                "current_translation": current,
                "rejected_translation": draft if structural else None,
                "triggers": triggers,
            }
            repair_messages = [
                {"role": "system", "content": REVIEW_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
            if sum(len(m["content"]) for m in repair_messages) > self.capacity.input_characters:
                raise TranslationError("定向复核超过已验证输入能力；未发布稿件。")
            self.last_response = None
            response = None
            try:
                response = self._request(
                    f"conditional:repair:{chapter.id}:{attempt}", repair_messages, temperature=0.3
                )
                value = json.loads(response)
                if (
                    not isinstance(value, dict)
                    or set(value) != {"decision", "units"}
                    or value["decision"] not in {"accept", "replace"}
                ):
                    raise TranslationError("定向复核响应结构无效。")
                if structural and value["decision"] != "replace":
                    raise TranslationError("缺失或截断的来源必须获得完整替换。")
                replacements = self._parse_units(json.dumps({"units": value["units"]}), affected)
                if value["decision"] == "accept" and replacements != current:
                    raise TranslationError("接受现有译文时不得改写。")
                remaining_signals = []
                for replacement in replacements:
                    replacement_source = "\n".join(
                        unit["text"]
                        for unit in full_source
                        if unit["id"] in replacement["source_ids"]
                    )
                    remaining_signals.extend(_signals(replacement_source, replacement["text"]))
                if value["decision"] == "replace" and any(
                    signal != "number_association" for signal in remaining_signals
                ):
                    # Lexical heuristics cannot prove a semantic defect. Ask the next
                    # bounded check to judge this structurally valid replacement itself.
                    self._event(
                        chapter=chapter.id,
                        triggers=triggers,
                        affected_source_ids=affected,
                        attempt=attempt,
                        result="check_required",
                        decision=value["decision"],
                        remaining_signals=sorted(set(remaining_signals)),
                    )
                    if _hash(response) == previous:
                        break
                    previous = _hash(response)
                    draft = response
                    current = replacements
                    structural = False
                    triggers = sorted(set(remaining_signals))
                    continue
            except (TranslationError, ValueError, TypeError) as error:
                if response is None and self.last_response is None:
                    raise
                # Cached observations must pass the same review contract on replay.
                # They have no fresh transport response, but their rejection still
                # consumes this bounded slot and leads to the next repair slot.
                rejected = response if response is not None else self.last_response.text or ""
                self._event(
                    chapter=chapter.id,
                    triggers=triggers,
                    affected_source_ids=affected,
                    attempt=attempt,
                    result="rejected",
                    rejected_response=rejected,
                    validation=type(error).__name__,
                )
                if _hash(rejected) == previous:
                    break
                previous = _hash(rejected)
                draft = rejected
                continue
            rows = [*accepted, *replacements]
            positions = {id: index for index, id in enumerate(chapter.source_ids)}
            rows.sort(key=lambda row: positions[row["source_ids"][0]])
            self._parse_units(json.dumps({"units": rows}), chapter.source_ids)
            changed = replacements != original_current
            self.repairs += int(changed)
            self._event(
                chapter=chapter.id,
                triggers=triggers,
                affected_source_ids=affected,
                attempt=attempt,
                result="repaired" if changed else "accepted",
                decision=value["decision"],
                semantic_resolution="automatic_review_" + value["decision"],
                remaining_signals=sorted(set(remaining_signals)),
            )
            return rows
        raise TranslationError("定向复核未解决来源结构或待核实语义信号；已停止，未发布稿件。")

    def _run(self, transcript):
        result = super()._run(transcript)
        result.provenance["variant"] = "C-conditional"
        result.provenance["review"] = {
            "policy": asdict(self.review_policy),
            "repairs": self.repairs,
            "review_attempts": self.review_attempts,
            "events": self.review_events,
        }
        return result
