"""Conservative vetoes for narrative updates; finite rules, never semantic approval."""
from __future__ import annotations
import re
from .condition_parser import ConditionParser
from .temporal_resolver import TemporalResolver


def binding(text):
    from ..claims import SLOTS
    normalized=text.strip()
    unknown=lambda reason:{'known':False,'reason':reason}
    if not re.match(r'^(?:用户(?=\s|[：:]|喜欢|偏好|不|希望|回答|回复|住|居住|预算|目标|平时|默认|现在|工作|学习|面试|通常|计划|打算|准备)|我(?!的(?:朋友|同事|家人))|I\b|My\b)',normalized,re.I):
        return unknown('subject_not_explicit_self')
    if re.search(r'朋友|同事|家人|我妈|我爸|父亲|母亲|妈妈|爸爸|妻子|丈夫|孩子|同学|室友|他|她|\b(?:he|she|they|friend|colleague|mother|father|sister|brother|partner)\b',normalized,re.I):
        return unknown('third_party_or_coreference')
    if re.search(r'不太|不一定|未必|可能|也许|不确定|不是不|并非不|不代表|不意味着|\b(?:maybe|might|perhaps)\b',normalized,re.I):
        return unknown('uncertain_or_qualified_statement')
    if re.search(r'本次|这次|暂时|临时|\bthis (?:session|chat|interview)\b',normalized,re.I):
        return unknown('temporary_lifecycle_needs_explicit_binding')
    clauses=ConditionParser().clauses(normalized)
    scopes={str(sorted(c.scope.items())) for c in clauses}
    if any(c.ambiguous for c in clauses) or len(scopes)!=1:
        return unknown('condition_unresolved_or_multiple')
    scope=clauses[0].scope if clauses else {}
    if scope.get('temporary'):
        return unknown('temporary_lifecycle_needs_explicit_binding')
    if re.search(r'去年|上个月|以前|曾经|后来|曾住|last year|last month|previously',normalized,re.I):
        return unknown('relative_fact_time_unresolved')
    hits=[]
    if re.search(r'回答|回复|\brepl(?:y|ies)\b|\brespond',normalized,re.I):
        languages=re.findall(r'中文|汉语|普通话|英文|英语|\bEnglish\b|\bChinese\b',normalized,re.I)
        values={'英文' if v.lower() in {'英文','英语','english'} else '中文' for v in languages}
        if len(values)>1:return unknown('multiple_language_values')
        if len(values)==1:hits.append(('response_language',next(iter(values))))
        details=re.findall(r'简洁|简短|简要|详细|详尽|\bconcise\b|\bdetailed\b',normalized,re.I)
        values={'简洁' if v.lower() in {'简洁','简短','简要','concise'} else '详细' for v in details}
        if len(values)>1:return unknown('multiple_detail_values')
        if len(values)==1:hits.append(('response_detail',next(iter(values))))
    residence=re.search(r'(?:住在|居住在|\blive(?:s)? in )([^，,。；;！？!?\n]{1,30})',normalized,re.I)
    if residence:hits.append(('residence',residence.group(1).strip()))
    if re.search(r'喜欢|偏好|讨厌|\blike|\bprefer',normalized,re.I):
        food=re.findall(r'乌龙茶|绿茶|红茶|咖啡|牛奶|素食|\bcoffee\b|\btea\b',normalized,re.I)
        if len(set(food))==1:hits.append(('diet',food[0].casefold()))
        hobby=re.findall(r'跑步|游泳|阅读|\brunning\b|\bswimming\b',normalized,re.I)
        if len(set(hobby))==1:hits.append(('hobby',hobby[0].casefold()))
    numbers=re.findall(r'(?<![a-z])[-+]?\d+(?:\.\d+)?',normalized,re.I)
    if '预算' in normalized and len(numbers)==1:hits.append(('budget',numbers[0]))
    if re.search(r'目标.*每周.*公里',normalized) and len(numbers)==1:
        hits.append(('project_goal',numbers[0]))
    if len(hits)!=1:return unknown('closed_slot_or_value_not_unique')
    predicate,value=hits[0]
    event=TemporalResolver().resolve(normalized)
    modality='plan' if re.search(r'计划|打算|准备|想去|\bplan to\b|\bwant to\b',normalized,re.I) else 'statement'
    polarity='negative' if re.search(r'不(?:喜欢|偏好|希望|住|喝)|不再|讨厌|do not|don.t|never|no longer',normalized,re.I) else 'positive'
    return {'known':True,'subject':'self','predicate':predicate,'value':value,'scope':scope,
        'modality':modality,'polarity':polarity,'fact_time':event.model_dump() if event else None,
        'multi_value':SLOTS[predicate], 'numeric_goal_key':re.sub(r'[-+]?\d+(?:\.\d+)?','<value>',normalized) if predicate=='project_goal' else None,
        'semantic_support_verified':False}


def compatibility(new,old):
    a,b=binding(new),binding(old)
    if not a['known'] or not b['known']:return False,a.get('reason') or b.get('reason'),a,b
    for key in ('subject','predicate','scope','modality','fact_time'):
        if a[key]!=b[key]:return False,key+'_mismatch',a,b
    return True,'same_bound_context',a,b


def decision(new,old,action,source,*,candidate_count=None):
    compatible,reason,a,b=compatibility(new,old)
    if not compatible:return {'allowed':False,'reason':reason}
    if action=='reinforce':
        allowed=a['value']==b['value'] and a['polarity']==b['polarity']
        return {'allowed':allowed,'reason':'same_value_polarity' if allowed else 'value_or_polarity_mismatch'}
    if action=='supersede':
        explicit_replacement=bool(re.search(r'改为|改成|换成|switched from|instead of',new,re.I)) and candidate_count==1
        if a['multi_value'] and a['value']!=b['value'] and not explicit_replacement and not (a['numeric_goal_key'] and a['numeric_goal_key']==b['numeric_goal_key']):
            return {'allowed':False,'reason':'multiple_values_should_coexist'}
        explicit=source in {'manual','reviewed_conversation'} or bool(re.search(r'现在|改为|不再|改成|moved|no longer|now ',new,re.I))
        return {'allowed':explicit,'reason':'explicit_change_or_review' if explicit else 'transition_needs_explicit_review'}
    return {'allowed':True,'reason':'independent_create'}
