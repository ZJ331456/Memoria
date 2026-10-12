"""Stable selection trace including every candidate and its decision."""
def decision(item, reason, step=None, **values):
    return {"id": item["id"], "decision": reason, "step": step, **values}
