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
        if self.precision not in {"exact", "day", "month", "year", "range"}:
            raise ValueError("unsupported event precision")
        if len(self.quote) > 200:
            raise ValueError("event time quote too long")
        a, b = datetime.fromisoformat(self.lower), datetime.fromisoformat(self.upper)
        if not a.tzinfo or not b.tzinfo or a >= b:
            raise ValueError("event time needs an ordered timezone range")
        return self


class TemporalResolver:
    def resolve_event(self, text: str, reference: datetime | None = None) -> dict:
        """Resolve mentioned events/plans, not state validity. Reference is ORIGINAL source time."""
        if reference is not None and reference.tzinfo is None:
            raise ValueError('Original source time requires a timezone')
        absolute = list(re.finditer(r"(?<!\d)(?:19|20)\d{2}(?:年(?:\d{1,2}月(?:\d{1,2}[日号])?)?|-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?)", text))
        relative = list(re.finditer(r'前年|去年|今年|明年|上个月|这个月|本月|下个月|前天|昨天|今天|明天|后天|上周|本周|这周|下周|[一二三四五六七八九十两\d]{1,3}年前|\byesterday\b|\btoday\b|\btomorrow\b|\blast week\b',text,re.I))
        if len(absolute)==2 and not relative and re.fullmatch(r'\s*(?:至|到|—|–|~|～|to)\s*',text[absolute[0].end():absolute[1].start()],re.I):
            left,right=(self.resolve(m.group().replace('号','日')) for m in absolute)
            if left and right and datetime.fromisoformat(left.lower)<datetime.fromisoformat(right.upper):
                return {'event_time':EventTime(precision='range',lower=left.lower,upper=right.upper,
                    quote=text[absolute[0].start():absolute[1].end()]),'reason':'explicit_calendar_range'}
            return {'event_time':None,'reason':'invalid_calendar_range'}
        if len(absolute)+len(relative)>1:return {'event_time':None,'reason':'multiple_time_expressions'}
        if absolute:
            if re.search(r'(?:至|到|~|～|—|–)\s*\d{1,2}月',text):return {'event_time':None,'reason':'unsupported_range_endpoint'}
            if text[absolute[0].end():].startswith('T'):return {'event_time':None,'reason':'missing_exact_timezone'}
            value=self.resolve(absolute[0].group().replace('号','日'))
            if value:value.quote=absolute[0].group()
            return {'event_time':value,'reason':'explicit_event_time' if value else 'invalid_calendar_date'}
        if relative:
            if reference is None:return {'event_time':None,'reason':'missing_original_source_time'}
            word=relative[0].group().lower();base=reference.replace(hour=0,minute=0,second=0,microsecond=0)
            try:
                if word in {'前天','昨天','今天','明天','后天','yesterday','today','tomorrow'}:
                    lower=base+timedelta(days={'前天':-2,'昨天':-1,'今天':0,'明天':1,'后天':2,'yesterday':-1,'today':0,'tomorrow':1}[word])
                    upper=lower+timedelta(days=1);precision='day'
                elif word in {'上周','本周','这周','下周','last week'}:
                    lower=base-timedelta(days=base.weekday())+timedelta(weeks={'上周':-1,'last week':-1,'本周':0,'这周':0,'下周':1}[word])
                    upper=lower+timedelta(days=7);precision='range'
                elif word in {'上个月','这个月','本月','下个月'}:
                    index=base.year*12+base.month-1+{'上个月':-1,'这个月':0,'本月':0,'下个月':1}[word]
                    y,m=divmod(index,12);lower=base.replace(year=y,month=m+1,day=1)
                    upper=lower.replace(year=y+1,month=1) if m==11 else lower.replace(month=m+2);precision='month'
                else:
                    offsets={'前年':-2,'去年':-1,'今年':0,'明年':1}
                    if word in offsets:delta=offsets[word]
                    else:
                        token=word[:-2];numbers={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
                        if token.isdigit():amount=int(token)
                        elif '十' in token:
                            a,_,b=token.partition('十');amount=numbers.get(a,1)*10+numbers.get(b,0)
                        else:amount=numbers.get(token,0)
                        if not 1<=amount<=99:raise ValueError('unsupported relative year')
                        delta=-amount
                    lower=base.replace(year=base.year+delta,month=1,day=1);upper=lower.replace(year=lower.year+1);precision='year'
                return {'event_time':EventTime(precision=precision,lower=lower.isoformat(),upper=upper.isoformat(),quote=relative[0].group()),'reason':'relative_to_original_source_time'}
            except (ValueError,OverflowError):return {'event_time':None,'reason':'invalid_calendar_date'}
        return {'event_time':None,'reason':'unsupported_time_expression' if re.search(r'最近|上次|过去|[一二三四五六七八九十\d]+个月前|\d{1,2}月|\d{4}[/-]',text) else 'no_explicit_event_time'}

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
