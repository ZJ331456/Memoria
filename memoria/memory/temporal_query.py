"""Conservative Chinese query parsing. Calendar ranges are never guessed instants."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

LOCAL_ZONE = timezone(timedelta(hours=8))
OTHER_SUBJECT = re.compile(r"他|她|他们|别人|朋友|同事|父亲|母亲|好友|家人|伴侣|室友|孩子|老板|同学|对象")
PATTERNS = {
    "residence": r"住在哪|住哪里|居住|住在|搬到|搬去|搬家",
    "response_language": r"什么语言|哪种语言|回答语言|回复语言|用中文|用英文|用英语|用汉语",
    "response_detail": r"详细度|回答风格|回复风格|简洁|简短|详细回答",
    "occupation": r"职业|做什么工作", "school": r"学校|哪所大学",
    "diet": r"饮食|忌口|吃什么", "hobby": r"爱好|兴趣",
    "learning_goal": r"学习目标|学习计划", "project_goal": r"项目目标",
    "work_habit": r"工作习惯", "time_preference": r"时间偏好", "budget": r"预算",
}


def parse_query(text: str, reference: datetime | None = None) -> dict:
    reference = reference or datetime.now(LOCAL_ZONE)
    if reference.tzinfo is None:
        raise ValueError("reference requires timezone")
    reference = reference.astimezone(LOCAL_ZONE)
    predicates = [p for p, pattern in PATTERNS.items() if re.search(pattern, text)]
    interview = bool(re.search(r"面试模拟|模拟面试|面试时|面试的时候|面试中", text))
    default = bool(re.search(r"平时|默认|通常情况下|一般情况下", text))
    context = {"activity": "interview"} if interview else {}
    result = {"parser": "zh-calendar-context-rules-v2", "subject_id": "self", "predicates": predicates,
              "context": context, "scope_mode": "default" if default and not interview else "applicable",
              "intent": "current", "axis": "fact", "precision": "unknown",
              "range_start": None, "range_end": None, "requires_clarification": False,
              "condition_requires_clarification": False,
              "subject_requires_clarification": bool(OTHER_SUBJECT.search(text)),
              "reason": "current_or_general_query", "reference_time": reference.isoformat()}
    from .condition_parser import ConditionParser
    # Calendar query qualifiers are handled by the time parser, not activity scopes.
    condition_text = re.sub(r"(?:去年|前年|今年|上个月|这个月|昨天|前天|上周|最近|当时|\d{4}年(?:\d{1,2}月)?(?:\d{1,2}日)?)(?:的时候|期间|时)", "", text)
    clauses = ConditionParser().clauses(condition_text)
    if any(c.ambiguous for c in clauses):
        result.update(requires_clarification=True, condition_requires_clarification=True, reason="unsupported_query_condition")
    scopes = [c.scope for c in clauses if c.scope]
    for scope in scopes:
        for key, value in scope.items():
            if key in result["context"] and result["context"][key] != value:
                result.update(requires_clarification=True, condition_requires_clarification=True, reason="multiple_query_conditions")
            result["context"][key] = value
    if default and result["context"]:
        result.update(requires_clarification=True, condition_requires_clarification=True, reason="default_and_specific_context_ambiguous")
        return result
    if re.search(r"你.*(?:认为|知道|记得|获知|以为|觉得|记忆中)|当时.*(?:认为|知道)", text):
        result["axis"] = "recorded"
    transition = bool(re.search(r"什么时候|何时", text) and re.search(r"搬|变|改|开始|停止", text))
    anchors = re.search(r"从([^，。！？?]{1,30}?)搬(?:到|去)([^，。！？?]{1,30})", text)
    result.update(transition_from=anchors.group(1).strip() if anchors else None,
                  transition_to=anchors.group(2).strip().rstrip("的了") if anchors else None)
    if transition:
        result.update(intent="transition", reason="transition_timeline; unknown event time stays unknown")
    years = re.findall(r"(?<!\d)((?:19|20)\d{2})年(?:(\d{1,2})月)?(?:(\d{1,2})日)?", text)
    relative = [w for w in ("去年", "前年", "今年", "上个月", "这个月", "昨天", "前天") if w in text]
    if len(years) + len(relative) > 1:
        result.update(intent="history", requires_clarification=True, reason="multiple_time_expressions")
        return result
    year, month, day, precision = None, None, None, None
    if years:
        y, m, d = years[0]
        year, month, day = int(y), int(m) if m else None, int(d) if d else None
        precision = "day" if day else "month" if month else "year"
    elif relative:
        word = relative[0]
        year, month, day = reference.year, reference.month, reference.day
        if word in {"去年", "前年", "今年"}:
            year -= {"去年": 1, "前年": 2, "今年": 0}[word]
            month, day, precision = None, None, "year"
        elif word in {"上个月", "这个月"}:
            if word == "上个月":
                year, month = (year - 1, 12) if month == 1 else (year, month - 1)
            day, precision = None, "month"
        else:
            dt = reference - timedelta(days=1 if word == "昨天" else 2)
            year, month, day, precision = dt.year, dt.month, dt.day, "day"
    if precision:
        try:
            start = datetime(year, month or 1, day or 1, tzinfo=LOCAL_ZONE)
            end = (datetime(year + 1, 1, 1, tzinfo=LOCAL_ZONE) if precision == "year" else
                   datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=LOCAL_ZONE) if precision == "month" else
                   start + timedelta(days=1))
        except ValueError:
            result.update(intent="history", requires_clarification=True, reason="invalid_calendar_date")
            return result
        result.update(intent="history" if not transition else "transition", precision=precision,
                      range_start=start.isoformat(), range_end=end.isoformat(), reason="calendar_range; not a point assertion")
        if result["axis"] == "recorded":
            result.update(reason="recorded_calendar_range; knowledge timeline not a guessed cutoff")
    elif re.search(r"最近|以前|当时|上周|过去|那时候", text):
        result.update(intent="history", requires_clarification=True, reason="ambiguous_time_requires_clarification")
    if re.search(r"(?<!\d)\d{4}[/-]\d{1,2}|[一二三四五六七八九十\d]+年前|[一二三四五六七八九十\d]+个月前|[零〇一二三四五六七八九]{4}年|上上|明年|明天|下个月|年初|月底|年末|曾经|之前|之后", text):
        result.update(intent="history", requires_clarification=True, reason="unsupported_time_expression")
    if re.search(r"什么时候|何时", text) and not transition:
        result.update(intent="history", requires_clarification=True, reason="event_time_requires_timeline_or_clarification")
    if len(re.findall(r"\d{4}年", text)) != len(years):
        result.update(intent="history", requires_clarification=True, reason="unsupported_calendar_year")
    if result["subject_requires_clarification"]:
        if not result["requires_clarification"]:
            result["reason"] = "subject_requires_explicit_entity_id"
        result.update(requires_clarification=True, subject_requires_clarification=True)
    return result


class TemporalQueryParser:
    """Expose both time axes without replacing intervals by manufactured points."""
    def parse(self, text: str, reference: datetime | None = None) -> dict:
        result = parse_query(text, reference)
        result["parser"] = "zh-calendar-context-rules-v3"
        result["temporal_intent"] = ("transition" if result["intent"] == "transition" else
            "historical_knowledge" if result["intent"] == "history" and result["axis"] == "recorded" else
            "historical_fact" if result["intent"] == "history" else "current")
        result.update(fact_as_of=None, recorded_as_of=None, time_precision=result["precision"],
                      fact_interval=None, recorded_interval=None)
        result["predicate"] = result["predicates"][0] if len(result["predicates"]) == 1 else None
        if result["range_start"]:
            result[result["axis"] + "_interval"] = {"lower":result["range_start"],"upper":result["range_end"],"bounds":"[lower,upper)"}
        return result
