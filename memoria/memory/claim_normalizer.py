"""Closed-slot normalization; preserve raw model values in the extraction trace."""
import re
import unicodedata

ALIASES = {
    "response_language": {"中文": "中文", "汉语": "中文", "普通话": "中文", "chinese": "中文", "zh": "中文",
                          "英文": "英文", "英语": "英文", "english": "英文", "en": "英文"},
    "response_detail": {"简洁": "简洁", "简短": "简洁", "简要": "简洁", "concise": "简洁",
                        "详细": "详细", "详尽": "详细", "detailed": "详细"},
}


def value_key(value):
    return unicodedata.normalize("NFKC", value).strip()


def aliases(predicate, value):
    key = value_key(value)
    canonical = ALIASES.get(predicate, {}).get(key.casefold(), key)
    return sorted({canonical, key, *[a for a, v in ALIASES.get(predicate, {}).items() if v == canonical]}, key=len, reverse=True)


def mentions(predicate, value, text):
    patterns = []
    for alias in aliases(predicate, value):
        escaped = re.escape(alias)
        if alias.isascii():
            escaped = r"(?<![a-zA-Z0-9_])" + escaped + r"(?![a-zA-Z0-9_])"
        patterns.append(escaped)
    return list(re.finditer("|".join(patterns), text, flags=re.I))


def preference_desire(predicate, text):
    return predicate in {"response_language", "response_detail"} and bool(re.search(r"(?:希望|想要)(?:你)?(?:回答|回复|用|使用|尽量)", text))


class ClaimNormalizer:
    def normalize(self, predicate, value, modality, quote):
        from ..claims import SLOTS
        if predicate not in SLOTS:
            raise ValueError("unsupported predicate")
        raw = value
        value = value_key(value)
        value = ALIASES.get(predicate, {}).get(value.casefold(), value)
        if not value:
            raise ValueError("empty normalized value")
        if modality == "plan" and preference_desire(predicate, quote):
            modality = "statement"
        return {"predicate": predicate, "value": value, "modality": modality,
                "trace": {"raw_value": raw, "normalized_value": value, "rule": "closed-slot-alias-v1"}}
