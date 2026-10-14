from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class ToolAuthorization:
    allowed_write_tools: set[str] = field(default_factory=set)
    reason: str = "no explicit write intent"

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.allowed_write_tools


class ToolPolicy:
    """Map explicit user intent to the narrowest write-tool capability."""

    _MEMORIZE = re.compile(r"记住|保存.{0,8}(偏好|信息|记忆)|添加记忆|remember|memorize", re.I)
    _FORGET = re.compile(r"忘掉|忘记|删除.{0,8}(记忆|信息|偏好)|forget|delete.{0,8}memory", re.I)
    _QUOTED = re.compile(r'"[^"\n]*"|“[^”]*”|「[^」]*」|『[^』]*』|`[^`]*`')
    _NEGATIVE = re.compile(r"不要|别|不许|禁止|无需|不必|不能|不准|不想|\b(?:not|never|don't|dont|cannot|can't)\b", re.I)
    _ZH_PREFIX = r"(?:(?:请|麻烦你?|帮我|替我|务必|一定要)\s*)*"
    _COMMANDS = {
        'memorize': re.compile(
            '^' + _ZH_PREFIX + r'(?:记住|保存.{0,8}(?:偏好|信息|记忆)|添加记忆)'
            r'|^(?:(?:please|also)\s+)*(?:remember|memorize)\b'
            r'|^(?:can|could|would|will)\s+you\s+(?:please\s+(?:remember|memorize)\b|memorize\b|remember\s+that\b)', re.I),
        'forget_memory': re.compile(
            '^' + _ZH_PREFIX + r'(?:忘掉|忘记|删除.{0,8}(?:记忆|信息|偏好))'
            r'|^(?:(?:please|also)\s+|(?:can|could|would|will)\s+you\s+)*(?:forget\b|delete.{0,8}memory\b)', re.I),
    }

    def authorize(self, user_text: str) -> ToolAuthorization:
        allowed, denied = set(), set()
        # Only direct commands grant a capability. Quoted examples, recollections
        # and negated requests must not become authorization for a model tool call.
        text = self._QUOTED.sub("", user_text)
        for clause in re.findall(r"[^。！？!?；;，,\n]+[。！？!?；;，,]?", text):
            clause = clause.strip()
            for name, pattern in (("memorize", self._MEMORIZE), ("forget_memory", self._FORGET)):
                matches = list(pattern.finditer(clause))
                if any(self._NEGATIVE.search(clause[:match.start()]) for match in matches):
                    denied.add(name)
                if (name == 'memorize' and clause.endswith(('?', '？'))
                        and re.match(r'^(?:remember\b|记住)', clause, re.I)):
                    continue  # Bare "remember ...?" is an ambiguous recall question.
                if matches and self._COMMANDS[name].search(clause):
                    allowed.add(name)
        allowed -= denied
        reason = "explicit user intent: " + ",".join(sorted(allowed)) if allowed else "no explicit write intent"
        return ToolAuthorization(allowed, reason)
