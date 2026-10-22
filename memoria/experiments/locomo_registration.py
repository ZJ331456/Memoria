"""Fail-before-call registration checks for diagnostic LoCoMo v3 runs."""

from dataclasses import asdict
import hashlib
import inspect
import json
from pathlib import Path
from importlib.metadata import version

from .history import canonical
from .records import digest_file, model_snapshot


def scoring_identity():
    from nltk.stem import PorterStemmer
    from .locomo_eval import answer_f1, sha

    return {
        "policy": "upstream-compatible-porter-f1-and-literal-refusal-v1",
        "implementation_sha256": sha(inspect.getsource(answer_f1)),
        "stemmer_sha256": sha(inspect.getsource(PorterStemmer)),
        "nltk": version("nltk"),
    }


def validate_registration(
    path,
    root,
    source,
    protocol,
    settings,
    roles,
    *,
    fallback,
    concurrency,
    embedding_model,
    candidate_limit,
    anchor_limit,
    evidence_chars,
    cache_layout="legacy"
):
    from .locomo_risk import PROTOCOL

    path = Path(path).resolve(strict=True)
    registration = json.loads(path.read_text(encoding="utf-8"))
    if (
        registration["protocol"] != PROTOCOL
        or registration["claim"] != "same-question diagnostic retest"
    ):
        raise ValueError(
            "Registration must explicitly identify diagnostic v3, never v2 reproduction"
        )
    if (
        registration["unseen_generalization"] is not False
        or fallback not in registration["methods"]
    ):
        raise ValueError("Invalid diagnostic registration claim/method")
    study = registration.get("cache_layout_study")
    if cache_layout != "legacy" and (not study or cache_layout not in study["allowed_layouts"]):
        raise ValueError(
            "Cache layout requires its own diagnostic registration; frozen v3 cannot change prompts"
        )
    if study:
        from ..llm.deepseek_cache import COMMON_SYSTEM
        from .locomo import sha

        if sha(COMMON_SYSTEM) != study["common_system_sha256"]:
            raise ValueError("Registered DeepSeek common prefix changed")
    if (
        fallback in {"targeted", "adaptive_risk"}
        and protocol.get("verification_policy") != registration["risk_verification_policy"]
    ):
        raise ValueError("Actual verifier policy differs from v3 registration")
    if Path(source).resolve() != (root / registration["input"]).resolve():
        raise ValueError("Use the canonical input path in the registration")
    frozen = registration["inputs"][protocol["scope"]]
    for key in (
        "source_sha256",
        "inputs_sha256",
        "histories_sha256",
        "gold_sha256",
        "requested_cases",
    ):
        if protocol[key] != frozen[key]:
            raise ValueError("Registered input fingerprint differs: " + key)
    observed = {
        "concurrency": concurrency,
        "embedding_model": embedding_model,
        "candidate_limit": candidate_limit,
        "anchor_limit": anchor_limit,
        "evidence_chars": evidence_chars,
    }
    if observed != registration["execution"]:
        raise ValueError(
            "Pass the registered concurrency, embedding and retrieval limits explicitly"
        )
    reader = roles["reader"]
    model = getattr(settings, reader.slot)
    if {
        **asdict(reader),
        "model": model.model,
        "endpoint": model.base_url.rstrip("/"),
    } != registration["reader"]:
        raise ValueError("Reader model/endpoint/generation differs from registration")
    if scoring_identity() != registration["scoring"]:
        raise ValueError("Scoring dependency differs from registration")
    if digest_file(root / registration["roles_file"]) != registration["roles_sha256"]:
        raise ValueError("Frozen role file changed")
    if (
        digest_file(root / registration["engineering_protocol_file"])
        != registration["engineering_protocol_sha256"]
    ):
        raise ValueError("Frozen v3 engineering protocol changed")
    snapshot = model_snapshot(root / "model" / embedding_model, root)
    if snapshot != registration["embedding_snapshot"]:
        raise ValueError("Embedding weights/tokenizer snapshot differs from registration")
    # Registration bytes and exact input hashes become part of the resume fingerprint.
    return {
        "sha256": digest_file(path),
        "id": registration["id"],
        "claim": registration["claim"],
        "unseen_generalization": False,
        "reader": registration["reader"],
        "scoring": registration["scoring"],
        "execution": observed,
        "risk_verification_policy": registration["risk_verification_policy"],
        **({"cache_layout_study": study} if study else {}),
        "inputs": frozen,
        "manifest_sha256": hashlib.sha256(canonical(registration).encode()).hexdigest(),
    }
