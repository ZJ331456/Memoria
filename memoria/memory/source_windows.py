"""Exact source slices with explicit omissions; derived text is a locator only."""
import re


def windows(text,query,*,width=900,hint=None):
    if len(text)<=width:return [(0,len(text))]
    from .lexical import token_sequence
    terms={t.casefold() for t in token_sequence(query) if len(t)>1}
    terms-={'什么','哪些','如何','怎么','what','which','does','the','and'}
    # Chinese sequences need bounded bigrams, rather than treating a full question as a token.
    for run in re.findall(r'[\u4e00-\u9fff]+',query):
        terms.update(run[i:i+2] for i in range(len(run)-1) if run[i:i+2] not in {'什么','哪些','如何','怎么','我的'})
    positions={0}
    lowered=text.casefold()
    for term in sorted(terms)[:64]:
        positions.update(m.start() for m in list(re.finditer(re.escape(term),lowered))[:64])
    if hint is not None:positions.add(max(0,min(hint,len(text)-1)))
    choices=[]
    for at in sorted(positions):
        start=max(0,at-width//4);end=min(len(text),start+width)
        # Snap inward only to nearby sentence boundaries; never glue distant fragments.
        if start:
            boundary=re.search(r'[。！？!?；;\n]',text[start:min(start+120,end)])
            if boundary:start+=boundary.end()
        if end<len(text):
            ends=list(re.finditer(r'[。！？!?；;\n]',text[max(start,end-120):end]))
            if ends:end=max(start,end-120)+ends[-1].end()
        if start>=end:continue
        sample=text[start:end].casefold()
        matched={t for t in terms if t in sample}
        choices.append((len(matched),start,end,matched))
    if not choices:return [(0,min(width,len(text)))]
    first=max(choices,key=lambda c:(c[0],int(hint is not None and c[1]<=hint<c[2]),-c[1]))
    selected=[(first[1],first[2])]
    # One complementary disjoint window is allowed; a parent still receives one RRF vote.
    extra=[c for c in choices if (c[2]<=first[1] or c[1]>=first[2]) and c[3]-first[3]]
    if extra:
        second=max(extra,key=lambda c:(len(c[3]-first[3]),c[0],-c[1]));selected.append((second[1],second[2]))
    return selected
