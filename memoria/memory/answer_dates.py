"""Bound source-clock derivation to an actual quoted event-time expression."""
import re


MONTH = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
YEAR = r'(?<!\d)(?:19|20)\d{2}(?!\d)'


def precision(text):
    # A calendar day used to locate an interval does not turn that interval into
    # an exact event day: "the week before 16 March" still has week precision.
    for unit, pattern in (
        ('week', r'\b(?:the\s+)?week\s+(?:before|after|of|containing|ending|starting)\b|(?:前|后|之前|之后)(?:的)?一(?:周|星期)'),
        ('month', r'\b(?:the\s+)?month\s+(?:before|after|of|containing|ending|starting)\b|(?:前|后|之前|之后)(?:的)?一个月'),
        ('year', r'\b(?:the\s+)?year\s+(?:before|after|of|containing|ending|starting)\b|(?:前|后|之前|之后)(?:的)?一年'),
    ):
        if re.search(pattern, text, re.I):return unit
    if re.search(r'\b(?:yesterday|today|tomorrow|that day|same day)\b|昨天|今天|明天|当天|\d{1,2}月\d{1,2}[日号]|'
                 r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}\s+'+MONTH+r'\b|\b'+MONTH+r'\s+\d{1,2}(?!\d)',text,re.I):
        return 'day'
    for unit, pattern in (
        ('week',r'\b(?:(?:last|this|next|that|same)\s+week|\w+\s+weeks?\s+ago)\b|上周|本周|这周|下周|上星期|这星期'),
        ('month',r'\b(?:(?:last|this|next|that|same)\s+month|\w+\s+months?\s+ago)\b|上个月|本月|这个月|下个月|\d{1,2}月|\b'+MONTH+r'\b'),
        ('year',r'\b(?:(?:last|this|next|that|same)\s+year|\w+\s+years?\s+ago)\b|去年|今年|明年|'+YEAR),
    ):
        if re.search(pattern,text,re.I):return unit
    if re.search(r'\b\w+\s+days?\s+ago\b|\d+天前',text,re.I):return 'day'
    return None


def speech_act_clock(question, quoted_events):
    """A quoted proposal/agreement is itself a source-time speech event.

    This cannot date the proposed activity or a previously reported acceptance.
    The question must ask for the decision/communication and the quote must
    perform that act, rather than merely describe a past decision.
    """
    return bool(re.search(r'\b(?:decid\w*|agree\w*|announc\w*|propos\w*|said|say)\b|决定|同意|提议|宣布',question,re.I)
        and any(re.search(r"\b(?:let['’]s|i agree|we agree|agreed|definitely|i hereby|we hereby)\b|我同意|我们同意|让我们|我宣布",text,re.I)
                for text in quoted_events))


def derivation_blockers(statement, quoted_events, clocks, *, clock_only, basis, question=''):
    # An explanation may name BOTH the source calendar and the derived event
    # value. Only a narrowly framed, actually matching source-date explanation
    # can exclude its anchor from event precision/arithmetic. The literal answer
    # and quotes are still checked in full by AnswerRisk.
    event_statement = statement
    explanation = re.search(
        r"\brelative to (?:the )?source(?:['’]s)? (?:declared |original )?date(?: of)?\s+"
        r"(?P<anchor>[^;.!?]{3,120}?),?\s+(?:that|this)\s+(?:is|means|gives)\b", statement, re.I)
    if explanation:
        anchor = re.sub(r'\W+', '', explanation['anchor']).casefold()
        if (re.search(YEAR,explanation['anchor']) and anchor and
                any(anchor in re.sub(r'\W+','',clock).casefold() for clock in clocks)):
            start,end=explanation.span('anchor')
            event_statement=statement[:start]+statement[end:]
    target = precision(event_statement)
    blockers=[]
    if clock_only and basis!='derived':blockers.append('clock_only_fact_requires_derived_basis')
    if target is None:
        if clock_only:blockers.append('clock_only_fact_has_no_event_time_value')
        return blockers
    allowed={'day':{'day','week','month','year'},'week':{'week'},'month':{'month','year'},'year':{'year'}}
    source_precisions={precision(text) for text in quoted_events}
    if clocks and speech_act_clock(question, quoted_events):source_precisions.add('day')
    # An adjudicated as-of derivation bounds its event by the same-source recording
    # clock when the event utterance itself carries no time expression at all. The
    # clock may ground a month/year answer only: an exact day is never established
    # by recency plus its recording clock, and an utterance that states its own
    # week/month precision still governs and cannot be sharpened by the clock.
    if basis=='derived' and quoted_events and all(precision(text) is None for text in quoted_events):
        for clock in clocks:
            unit=precision(clock)
            if unit in {'month','year'}:source_precisions.add(unit)
            elif unit=='day':source_precisions.add('month')
    if not any(target in allowed.get(p,set()) for p in source_precisions):
        blockers.append('event_time_expression_or_precision_not_bound')
    # A same-source clock is an arithmetic anchor, never proof that an un-timed
    # event occurred on its recording day. Resolve only unambiguous year offsets;
    # other arithmetic remains independent model advice within source precision.
    relations=[]
    for text in quoted_events:
        for token in re.findall(r'\b(?:last|this|next)\s+year\b|去年|今年|明年',text,re.I):
            relations.append(-1 if token.casefold() in {'last year','去年'} else 1 if token.casefold() in {'next year','明年'} else 0)
    source_years=set(re.findall(YEAR,' '.join(clocks)))
    stated_years=set(re.findall(YEAR,event_statement))
    if len(set(relations))==1 and len(source_years)==1 and stated_years:
        expected=str(int(next(iter(source_years)))+relations[0])
        if stated_years!={expected}:blockers.append('derived_calendar_year_mismatch')
    return blockers
