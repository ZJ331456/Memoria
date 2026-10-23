"""Stable, privacy-preserving failure attribution for the production Reader."""
import hashlib
import re


def contract_code(exc):
    if hasattr(exc, 'errors'):
        return 'schema_invalid'
    message = str(exc)
    for prefix, code in (
        ('incomplete final content:', 'output_incomplete'),
        ('Quote not present', 'quote_field_absent' if ' is absent on ' in message else 'quote_not_literal'),
        ('Temporal anchor must', 'date_anchor_partial'),
        ('All frozen query requirements', 'requirement_set_changed'),
        ('Answer units not completely covered', 'answer_units_uncovered'),
        ('Support claim differs', 'answer_unit_not_literal'),
        ('Timestamp alone', 'event_anchor_missing'),
        ('Derived date anchor', 'event_anchor_mismatch'),
        ('Repair cannot promote', 'negative_gate_repair_promotion'),
    ):
        if message.startswith(prefix):return code
    return 'contract_invalid'


class VerificationFailure(ValueError):
    def __init__(self, stage, code, exc):
        self.stage, self.code = stage, code
        self.detail_sha256 = hashlib.sha256(str(exc).encode()).hexdigest()
        # Never copy a source quote, missing answer token, key or provider body.
        super().__init__(f'{stage}:{code}')


def failure(stage, reason, exc=None):
    code = reason
    category = 'insufficient_evidence'
    if exc is not None:
        from ..observability.memory_trace import execution_failure
        operational=execution_failure(exc,stage)
        stage = getattr(exc, 'stage', stage)
        code = getattr(exc, 'code', None)
        if code:
            category = 'contract'
        elif isinstance(exc, TimeoutError):
            code, category = 'stage_timeout', 'timeout'
        else:
            from ..llm.types import ContextLengthError, ProviderError
            if isinstance(exc, ContextLengthError):code, category = 'request_or_shared_budget_exceeded', 'budget'
            elif isinstance(exc, ProviderError):
                http = re.search(r'\bHTTP\s+(\d{3})\b', str(exc), re.I)
                code, category = ('provider_http_'+http[1] if http else 'provider_failed'), 'provider'
                for cause in operational['cause_types']:
                    if cause in {'ConnectTimeout','ReadTimeout','WriteTimeout','PoolTimeout'}:
                        code,category='provider_'+cause.removesuffix('Timeout').lower()+'_timeout','timeout'
                        break
                    if cause in {'ConnectError','ReadError','WriteError','RemoteProtocolError'}:
                        code='provider_'+cause.lower()
            else:code, category = 'internal_'+type(exc).__name__.lower(), 'internal'
    elif reason in {'invalid_answer_schema','incomplete_reader_output','necessary_citations_omitted',
                    'evidence_not_presented','unseen_or_missing_source_citation','refusal_status_conflict'}:
        category='contract'
    elif reason=='no_bounded_live_candidates' or (reason and any(word in reason for word in ('budget','reservation'))):
        category = 'budget'
    elif reason and any(word in reason for word in ('source_changed','evidence_changed')):
        category='source_invalidated'
    elif reason and 'authority' in reason:
        category = 'source_or_authority'
    return {'stage':stage,'code':code,'category':category,
            'cause_types':operational['cause_types'] if exc else [],
            'exception_type':type(exc).__name__ if exc else None,
            'detail_sha256':getattr(exc,'detail_sha256',None),
            'technical_failure':category in {'contract','budget','timeout','provider','internal','source_invalidated'},
            'private_text_included':False}
