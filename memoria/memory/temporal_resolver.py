"""Ground explicit event precision without turning coarse dates into exact states."""
from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, ConfigDict, model_validator

ZONE = timezone(timedelta(hours=8))


class EventTime(BaseModel):
    model_config = ConfigDict(extra="forbid")
    precision: str
    lower: str
    upper: str
    quote: str
    @model_validator(mode="after")
    def valid_range(self):
        if self.precision not in {"exact", "day", "month", "year"}:
            raise ValueError("unsupported event precision")
        if len(self.quote) > 200:
            raise ValueError("event time quote too long")
        a, b = datetime.fromisoformat(self.lower), datetime.fromisoformat(self.upper)
        if not a.tzinfo or not b.tzinfo or a >= b:
            raise ValueError("event time needs an ordered timezone range")
        return self


class TemporalResolver:
    def resolve(self, text):
        exact = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})(?!\d)", text)
        dates = re.findall(r"(?<!\d)(?:19|20)\d{2}年(?:\d{1,2}月)?(?:\d{1,2}日)?|(?<!\d)(?:19|20)\d{2}-\d{2}-\d{2}(?![\dT])", text)
        if len(exact) + len(dates) != 1:
            return None
        quote = (exact or dates)[0]
        try:
            if exact:
                lower = datetime.fromisoformat(quote.replace("Z", "+00:00"))
                upper, precision = lower+timedelta(microseconds=1), "exact"
            else:
                numbers = list(map(int, re.findall(r"\d+", quote)))
                year, month, day = (numbers+[1, 1])[:3]
                lower = datetime(year, month, day, tzinfo=ZONE)
                precision = ["year", "month", "day"][len(numbers)-1]
                upper = (datetime(year+1, 1, 1, tzinfo=ZONE) if precision == "year" else
                         datetime(year+(month == 12), 1 if month == 12 else month+1, 1, tzinfo=ZONE) if precision == "month" else lower+timedelta(days=1))
            return EventTime(precision=precision, lower=lower.isoformat(), upper=upper.isoformat(), quote=quote)
        except ValueError:
            return None

    def assertion_fields(self, text, predicate=None, value=None):
        if predicate is not None:
            from .claim_normalizer import mentions
            clauses = [c for c in re.split(r"[。；;！？!?\n]", text) if mentions(predicate, value, c)]
            text = clauses[0] if len(clauses) == 1 else ""
        event = self.resolve(text)
        if event is None:
            return {"event_time": None, "fact_start": None, "time_precision": "unknown"}
        # An uncertain month/year is metadata, not proof of a particular first day.
        return {"event_time": event, "fact_start": event.lower if event.precision in {"day", "exact"} else None,
                "time_precision": event.precision if event.precision in {"day", "exact"} else "unknown"}
