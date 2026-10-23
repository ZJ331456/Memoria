"""Lossless source excerpts for necessary groups; no generated summaries or gold."""
from copy import deepcopy
import re


QUALIFIER = re.compile(r'\b(?:not|never|only|except|unless|if|but|however|before|after|yesterday|today|tomorrow|week|month|year)\b|不|没|仅|除非|但是|不过|之前|之后|昨天|今天|明天|年|月|日', re.I)


def compact(candidates, decision):
    """Keep exact required quotes, their sentence neighbours and all qualifiers.

    Each excerpt retains its absolute original span. Gaps are explicit and never
    available to literal-quote validation as an invented contiguous passage.
    Formed memories and identity evidence keep their complete representation.
    """
    quotes = {}
    for requirement in decision['requirements']:
        for source in requirement['sources']:
            if source.get('field', 'text') == 'text':
                quotes.setdefault(source['id'], []).append(source['quote'])
    result = deepcopy(candidates)
    for candidate in result:
        data = candidate['data']
        if data.get('representation') != 'source_quote' or data.get('excerpts'):
            continue
        text = data['content']
        spans = [(m.start(), m.end()) for m in re.finditer(r'[^.!?。！？\n]+(?:[.!?。！？]+|\n|$)\s*', text)]
        if not spans or not quotes.get(candidate['id']):
            continue
        needed = {0}
        for index, (start, end) in enumerate(spans):
            if QUALIFIER.search(text[start:end]):needed.add(index)
        for quote in quotes[candidate['id']]:
            # A text quote may instead be an unchanged entity identity proof.
            for match in re.finditer(re.escape(quote), text):
                needed.update(i for i, (start, end) in enumerate(spans)
                              if start < match.end() and end > match.start())
        context = {i for n in needed for i in (n-1, n, n+1) if 0 <= i < len(spans)}
        merged = []
        for index in sorted(context):
            start, end = spans[index]
            if merged and start <= merged[-1][1]:merged[-1][1] = end
            else:merged.append([start, end])
        excerpts = [{'start': data['start']+start, 'end': data['start']+end, 'text': text[start:end]}
                    for start, end in merged]
        if sum(len(e['text']) for e in excerpts) < len(text):
            data.update(content='', presentation='source_windows', excerpts=excerpts)
    return result
