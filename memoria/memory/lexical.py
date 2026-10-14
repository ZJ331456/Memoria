"""Shared bounded-language lexical representation for storage and retrieval."""
import re

STOP_TOKENS = {'我的','什么','是什么','用户','现在','偏好','目标','信息','the','what','your','user'}


def token_sequence(text: str) -> list[str]:
    lowered=text.lower()
    tokens=re.findall(r'[a-z0-9_]{2,}',lowered)
    for sequence in re.findall(r'[\u4e00-\u9fff]+',lowered):
        tokens.extend(sequence[i:i+2] for i in range(max(1,len(sequence)-1)))
        if 2<len(sequence)<=4: tokens.append(sequence)
    return [token for token in tokens if token and token not in STOP_TOKENS]
