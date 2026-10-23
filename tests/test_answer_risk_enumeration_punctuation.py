"""Synthetic answer-coverage regressions; no semantic quality labels."""
import pytest

from memoria.memory.answer_risk import AnswerRisk


def validate(answer, units):
    source='Alice and Bob recorded 2 visits.'
    value={'verdict':'supported','claims':[
        {'statement':unit,'relation':'supported','basis':'explicit',
         'sources':[{'id':'source','quote':source}],'reason':'Synthetic semantic advice.'}
        for unit in units], 'missing_queries':[], 'reason':'Synthetic.'}
    return AnswerRisk().validate(value,'Who was mentioned?',
        {'answer':answer,'evidence_ids':['source']},[{'id':'source','text':source}])


def test_uncovered_chinese_enumeration_comma_is_only_a_separator():
    result=validate('Alice、Bob。',['Alice','Bob'])
    assert result['accepted'] and result['answer_unit_coverage_verified']
    assert result['quote_integrity_verified'] and not result['semantic_entailment_verified']


@pytest.mark.parametrize('answer,units',[
    ('Alice、Bob、Charlie。',['Alice','Bob']),
    ('Alice、not Bob。',['Alice','Bob']),
    ('Alice、不是Bob。',['Alice','Bob']),
    ('Alice、-2。',['Alice','2']),
    ('Alice、+2。',['Alice','2']),
    ('Alice、−2。',['Alice','2']),
])
def test_enumeration_separator_cannot_hide_uncovered_content_negation_or_numeric_signs(answer,units):
    with pytest.raises(ValueError,match='Answer units not completely covered'):
        validate(answer,units)
