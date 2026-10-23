"""Reader projections retain routing receipts without unclosed summary facts."""
from copy import deepcopy


def reader_packet(packet):
    """A raw passage's organization may summarize other, unselected passages.

    Keep its receipt and original dependency checks, but never present those
    summaries as answer context. Gate retrieval hints remain a separate packet.
    Consolidations retain their complete, explicitly presented original support.
    """
    result = deepcopy(packet)
    for entry in result.get('evidence', []):
        provenance = entry.get('provenance', {})
        organization = provenance.get('formed_memory')
        if not organization:
            continue
        provenance['formed_memory'] = {
            'role': 'retrieval_receipt_only',
            'observations': [{'observation_id': item['observation_id']}
                             for item in organization.get('observations', [])],
            'summary_use': 'excluded_from_reader_unclosed_source_dependencies',
        }
    return result
