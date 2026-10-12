"""Finite, source-bound contexts with clause inheritance; not a free expression parser."""
from __future__ import annotations
import re
from dataclasses import dataclass

RULES = [(r"面试模拟|模拟面试|面试时|面试的时候|面试中", {"activity": "interview"}),
         (r"工作时|工作期间|上班时", {"activity": "work"}),
         (r"学习时|学习期间|上课时", {"activity": "study"}),
         (r"旅行时|旅行期间|旅游时|旅游期间", {"travel": True}),
         (r"在家时|在家期间", {"location": "home"}),
         (r"在办公室时|在办公室期间", {"location": "office"})]
DEFAULT = re.compile(r"平时|默认|通常情况下|一般情况下")
UNSUPPORTED = re.compile(r"如果|除非|只要|只有|当.{1,20}时|期间|的时候|\b(?:if|unless|only when|when)\b", re.I)


@dataclass
class Clause:
    start: int
    end: int
    scope: dict
    binding: tuple[int, int] | None
    ambiguous: bool = False


class ConditionParser:
    def clauses(self, text):
        active, binding, ambiguous, result = {}, None, False, []
        for match in re.finditer(r"[^，,。；;！？!?\n]+", text):
            part = match.group()
            markers = [(m, scope) for pattern, scope in RULES for m in re.finditer(pattern, part)]
            default = DEFAULT.search(part)
            if markers or default:
                active = {}
                ambiguous = bool(default and markers)
                positions = []
                for marker, scope in markers:
                    for key, value in scope.items():
                        if key in active and active[key] != value:
                            ambiguous = True
                        active[key] = value
                    positions.append((match.start()+marker.start(), match.start()+marker.end()))
                if positions:
                    binding = (min(p[0] for p in positions), max(p[1] for p in positions))
                else:
                    binding = None
                stripped = part
                for pattern, _ in RULES:
                    stripped = re.sub(pattern, "", stripped)
                ambiguous |= bool(UNSUPPORTED.search(stripped))
            elif UNSUPPORTED.search(part):
                ambiguous = True
            if re.search(r"这次|本次|暂时", part) and active:
                # Scope is known but its expiry is not. Never invent a TTL/date.
                active = {**active, "temporary": True}
                binding = (min(binding[0], match.start()) if binding else match.start(), match.end())
            result.append(Clause(match.start(), match.end(), dict(active), binding, ambiguous))
            # Full sentence punctuation ends inherited conditions; commas/conjunctions do not.
            if match.end() < len(text) and text[match.end()] in "。；;！？!?\n":
                active, binding, ambiguous = {}, None, False
        return result

    def resolve(self, text, start, end, predicate, value):
        from .claim_normalizer import mentions
        hits = mentions(predicate, value, text[start:end])
        if not hits:
            raise ValueError("normalized value not source-bound")
        clauses = self.clauses(text)
        applicable = [c for c in clauses if any(c.start <= start+h.start() < c.end for h in hits)]
        if not applicable or any(c.ambiguous for c in applicable):
            raise ValueError("unsupported or ambiguous source condition")
        if len({str(sorted(c.scope.items())) for c in applicable}) != 1:
            raise ValueError("value occurs under different conditions")
        chosen = applicable[-1]
        return {"scope": chosen.scope, "binding": chosen.binding, "clause_start": chosen.start,
                "clause_end": chosen.end, "origin": "source_clause_rules-v1"}
