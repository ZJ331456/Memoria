"""Offline reader tokenizer bound to trusted process-backend configuration."""
from functools import lru_cache


@lru_cache(maxsize=4)
def load(path: str):
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    return lambda text: tokenizer.encode(text, add_special_tokens=False)


def for_reader(llm):
    selected = getattr(getattr(llm, "settings", None), "main", None)
    if not selected or not selected.is_local_process():
        raise ValueError("exact reader tokenizer unavailable for remote/HTTP reader; use chars or utf8_bytes")
    from ...llm.backends.process_engine import model_path
    path = model_path(selected.model)
    if not path.is_dir():
        raise ValueError("configured local reader tokenizer path unavailable")
    try:
        return load(str(path))
    except Exception as exc:
        raise ValueError("configured reader tokenizer could not be loaded offline") from exc
