"""Benchmark adapters: turn a public dataset into items a memory system can be run on.

Each adapter yields items with a stable id, the question, the ground truth, a group label
and the ordered sources that existed *before* the question was asked. Sources carry their
original timestamps so a memory system can resolve relative dates itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

LOCOMO_TEXT_INPUT_POLICY = 'locomo-dialogue-text-v1'
LOCOMO_CAPTION_INPUT_POLICY = 'locomo-dialogue-and-blip-caption-v2'


@dataclass
class BenchmarkItem:
    """One question with the sources visible at the time it was asked."""

    id: str
    question: str
    ground_truth: str
    group: str = "all"
    sources: list[dict[str, Any]] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_session_date(raw: str) -> str | None:
    """Best-effort ISO timestamp for a free-form session date such as "1:56 pm on 8 May, 2023".

    A date that cannot be parsed is returned as None instead of a guessed value: an unknown
    source time must stay unknown rather than become a plausible wrong instant.
    """
    import re
    from datetime import datetime, timezone

    text = str(raw or "").strip()
    if not text:
        return None
    for pattern in (
        "%I:%M %p on %d %B, %Y",
        "%I:%M %p on %d %B %Y",
        "%d %B, %Y",
        "%d %B %Y",
        "%B %d, %Y",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc).isoformat()
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        try:
            return datetime(*(int(part) for part in match.groups()), tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None
    return None


def _session_date(conversation: dict, index: int) -> str:
    key = f"session_{index}_date_time"
    value = conversation.get(key)
    return str(value) if value else ""


def blip_caption_source(parent: dict[str, Any], caption: str) -> dict[str, Any]:
    """Attach existing dataset text as an independent machine annotation.

    No image is fetched, interpreted or OCRed. The parent's clock is not an
    image/event clock, and its speaker is not the author of the BLIP caption.
    Attribution lives in the text too because both production source batching
    and full-context readers render the same ``speaker/text/date`` contract.
    """
    if not isinstance(caption, str) or not caption.strip():
        raise ValueError('BLIP caption must be nonempty dataset text')
    attribution = json.dumps({'annotation_type': 'machine_generated_blip_image_caption',
        'parent_turn_id': parent['id'], 'parent_turn_speaker': parent['speaker'],
        'parent_relation': 'dataset attachment association only; not caption authorship or verified identity',
        'event_time': 'unknown', 'original_utterance': False}, ensure_ascii=False, sort_keys=True)
    return {'id': parent['id'] + '#blip-caption',
            'dia_id': parent['dia_id'] + '#blip-caption',
            'session': parent['session'], 'session_date_raw': '',
            'speaker': 'machine image caption (BLIP)',
            'text': '[Dataset machine annotation: ' + attribution + ']\n' + caption,
            'source_kind': 'machine_image_caption', 'parent_source_id': parent['id']}


def locomo_items(
    dataset: list[dict[str, Any]], *, conversations: int = 1, questions_per_conversation: int | None = None,
    include_captions: bool = False,
) -> Iterator[BenchmarkItem]:
    """LoCoMo: chronological dialogue turns plus QA pairs with evidence ids.

    Every QA is asked after the whole supplied conversation. Gold evidence is post-hoc
    diagnostic metadata, never a history cutoff or a retrieval input. Existing
    BLIP descriptions are excluded by default. Explicit caption mode is a new
    input protocol, not a reproduction of the earlier dialogue-only evaluation.
    """
    for index, conversation in enumerate(dataset[:conversations], 1):
        sample_id = conversation.get("sample_id", f"conv-{index}")
        sessions = sorted(
            int(part.split("_")[1])
            for part in conversation["conversation"]
            if part.startswith("session_")
            and part[len("session_") :].split("_")[0].isdigit()
            and "_date_time" not in part
        )
        turns: list[dict[str, Any]] = []
        dialogue_count, caption_count = 0, 0
        for number in sessions:
            date = _session_date(conversation["conversation"], number)
            for turn in conversation["conversation"][f"session_{number}"]:
                parent = {
                        "id": f"{sample_id}/{turn['dia_id']}",
                        "dia_id": turn["dia_id"],
                        "session": number,
                        "session_date_raw": date,
                        "speaker": turn.get("speaker") or "unknown",
                        "text": turn.get("text", ""),
                    }
                turns.append(parent)
                dialogue_count += 1
                caption = turn.get('blip_caption')
                if isinstance(caption, str) and caption.strip():
                    caption_count += 1
                    if include_captions:
                        turns.append(blip_caption_source(parent, caption))
        qa = conversation.get("qa") or []
        if questions_per_conversation:
            qa = qa[:questions_per_conversation]
        for order, entry in enumerate(qa):
            # The visible prefix is every turn of every session: LoCoMo asks its questions
            # after the whole conversation, so a real system must search all of it. The gold
            # evidence ids are kept for scoring only and never decide what is visible.
            yield BenchmarkItem(
                id=f"{sample_id}-q{order}",
                question=str(entry["question"]),
                ground_truth="Not mentioned." if int(entry.get("category",0))==5 else str(entry["answer"]),
                group=str(entry.get("category", "all")),
                sources=list(turns),
                extras={
                    "category": entry.get("category"),
                    "gold_evidence": entry.get("evidence") or [],
                    "conversation": sample_id,
                    "source_input_policy": LOCOMO_CAPTION_INPUT_POLICY if include_captions else LOCOMO_TEXT_INPUT_POLICY,
                    "source_input_audit": {'dialogue_turns': dialogue_count,
                        'nonempty_blip_captions': caption_count,
                        'imported_blip_captions': caption_count if include_captions else 0,
                        'excluded_blip_captions': 0 if include_captions else caption_count},
                },
            )


def longmemeval_items(dataset: list[dict[str, Any]], *, limit: int | None = None) -> Iterator[BenchmarkItem]:
    """LongMemEval: question/answer pairs over a long session history."""
    for order, entry in enumerate(dataset[: limit or len(dataset)]):
        haystack = entry.get("haystack_sessions") or []
        sessions = entry.get("haystack_dates") or []
        sources = []
        for number, session in enumerate(haystack):
            date = sessions[number] if number < len(sessions) else ""
            for turn_index, turn in enumerate(session):
                text = (turn.get("content") or turn.get("text") or "") if isinstance(turn, dict) else str(turn)
                role = turn.get("role") if isinstance(turn, dict) else "user"
                sources.append(
                    {
                        "id": f"{entry.get('question_id', order)}-s{number}-t{turn_index}",
                        "session": number,
                        "session_date_raw": date,
                        "speaker": role,
                        "text": text,
                    }
                )
        yield BenchmarkItem(
            id=str(entry.get("question_id", order)),
            question=entry.get("question", ""),
            ground_truth=entry.get("answer", ""),
            group=str(entry.get("question_type", "all")),
            sources=sources,
            extras={"evidence": entry.get("answer_session_ids", [])},
        )


ADAPTERS = {
    "locomo": locomo_items,
    "longmemeval": longmemeval_items,
}


def load_items(benchmark: str, path: str | Path, **kwargs) -> list[BenchmarkItem]:
    """Read a dataset and return benchmark items. Unknown benchmarks fail loudly."""
    if benchmark not in ADAPTERS:
        raise ValueError(f"Unsupported benchmark: {benchmark}")
    dataset = load_json(path)
    if isinstance(dataset, dict):
        dataset = dataset.get("data") or dataset.get("items") or []
    return list(ADAPTERS[benchmark](dataset, **kwargs))
