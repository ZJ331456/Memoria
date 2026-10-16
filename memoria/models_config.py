from __future__ import annotations

import re
import copy
from pathlib import Path
from typing import Any

import httpx

from .config import ModelConfig, Settings, SlotRole, _merge, _resolve
from .private_files import load_private_json


SLOT_PATHS = {
    "main": ("llm", "main"),
    "fast": ("llm", "fast"),
    "embedding": ("memory", "embedding"),
}


def override_path(settings: Settings) -> Path:
    return settings.model_config_file or settings.source


def model_document(data: dict) -> dict:
    return {parent: {slot: data.get(parent, {}).get(slot, {}) for slot in slots}
            for parent, slots in (("llm", ("main", "fast")), ("memory", ("embedding",)))}


def load_model_overrides(path: Path, owner: str | None = None) -> dict:
    from .config_store import read_document
    data = read_document(path)
    return model_document(data.get('accounts', {}).get(owner, {}) if owner else data)


def apply_account_settings(settings: Settings, directory: Path) -> Settings:
    from .accounts import user_directory
    directory = directory.resolve()
    owner = directory.name
    if not re.fullmatch(r"[a-f0-9]{32}", owner) or directory != user_directory(settings.data_root, owner).resolve():
        raise ValueError('Account configuration requires its server-owned UUID directory')
    # Switching identities must discard the previous account's credentials.
    # A fresh server/experiment Settings retains explicit in-memory run overrides.
    if settings.account_directory is not None and settings.account_directory != directory:
        settings.account_directory = None
        settings.main = ModelConfig('', '', '')
        settings.fast = ModelConfig('', '', '')
        settings.embedding = ModelConfig('', '', '')
        apply_overrides(settings, model_document(settings.model_settings))
    settings.account_directory = directory
    apply_overrides(settings)
    return settings


def validate_model_document(data: dict) -> None:
    allowed = {"model", "api_key", "base_url", "thinking", "backend", "device", "credential_profile"}
    if not isinstance(data, dict) or set(data) - {"llm", "memory"}:
        raise ValueError("Model file accepts only llm.main, llm.fast and memory.embedding")
    for parent, slots in (("llm", {"main", "fast"}), ("memory", {"embedding"})):
        table = data.get(parent, {})
        if not isinstance(table, dict) or set(table) - slots:
            raise ValueError("Unknown model slot in model configuration")
        for section in table.values():
            if not isinstance(section, dict) or set(section) - allowed:
                raise ValueError("Unknown field in model slot; use backend/model/base_url/api_key/device/thinking/credential_profile")
            if any(not isinstance(value, str) for value in section.values()):
                raise ValueError("Model slot fields must be strings")


def validate_model_configuration(data: dict) -> None:
    if not isinstance(data, dict) or set(data) - {'llm', 'memory', 'accounts'}:
        raise ValueError('Unknown table in models.toml')
    validate_model_document({key:value for key,value in data.items() if key != 'accounts'})
    accounts = data.get('accounts', {})
    if not isinstance(accounts, dict):
        raise ValueError('models.toml.accounts must be a UUID table')
    for owner, values in accounts.items():
        if not re.fullmatch(r'[a-f0-9]{32}', owner):
            raise ValueError('Model account table requires a server-owned UUID')
        validate_model_document(values)


def apply_overrides(settings: Settings, data: dict[str, Any] | None = None) -> Settings:
    payload = data if data is not None else model_document(settings.model_settings.get('accounts', {}).get(settings.account_directory.name, {}) if settings.account_directory else settings.model_settings)
    validate_model_document(payload)
    if not payload:
        return settings
    configured = {}
    for slot, keys in SLOT_PATHS.items():
        section = payload
        for key in keys:
            if not isinstance(section, dict):
                section = {}
                break
            section = section.get(key, {})
        if not isinstance(section, dict) or not section:
            continue
        current = getattr(settings, slot)
        # A key explicitly empty in a file clears inherited credentials. The UI
        # still omits blank keys when saving, preserving its "keep old key" rule.
        base_url = str(section.get("base_url", current.base_url)).strip().rstrip("/")
        backend = section.get("backend", current.backend)
        # Old account files remain valid. A changed URL without an explicit backend
        # is interpreted on its own, rather than retaining an inherited local mode.
        if "base_url" in section and "backend" not in section and base_url != current.base_url:
            backend = "auto"
        device = section.get("device", current.device)
        if "base_url" in section and not base_url.startswith("local://") and "device" not in section:
            device = "auto"
        if backend in {"bge", "transformers"} and "base_url" not in section:
            base_url = ""
        endpoint_changed = base_url != current.base_url or backend != current.backend
        api_key = _resolve(str(section.get("api_key", "" if endpoint_changed else current.api_key))).strip()
        model = str(section.get("model", current.model) or current.model)
        thinking = section.get("thinking", None if endpoint_changed else current.thinking)
        profile = section.get("credential_profile")
        if profile:
            owner_directory = settings.account_directory
            if owner_directory is None:
                raise ValueError('Private model profiles require an account-owned override')
            owner_directory = owner_directory.resolve()
            if not re.fullmatch(r"[a-f0-9]{32}", owner_directory.name) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", str(profile)):
                raise ValueError("Private model profiles require an account-owned override")
            secret_path = owner_directory / "secrets" / (str(profile) + ".research-model.dpapi")
            if not secret_path.resolve().is_relative_to(owner_directory) or secret_path.is_symlink():
                raise ValueError("Private model profile escapes account storage")
            saved = load_private_json(secret_path)
            if saved.get("owner_user_id") != owner_directory.name or saved.get("model") != model or str(saved.get("base_url", "")).rstrip("/") != base_url:
                raise ValueError("Private credential profile owner/model/endpoint mismatch")
            api_key = saved["api_key"]
            thinking = section.get("thinking", saved.get("thinking"))
        configured[slot] = ModelConfig(
                model=model,
                api_key=api_key,
                base_url=base_url,
                thinking=thinking,
                backend=backend,
                device=device,
            )
        configured[slot].validate_role("embedding" if slot == "embedding" else "llm")
    # An invalid later slot must not leave a partially switched runtime.
    for slot, config in configured.items():
        setattr(settings, slot, config)
    return settings


def public_models(settings: Settings) -> dict[str, Any]:
    def safe(value: ModelConfig, *, role: SlotRole) -> dict[str, Any]:
        from .llm.backends.resolve import backend_public_label

        payload = {
            "model": value.model,
            "base_url": value.base_url,
            "configured": value.is_ready(role=role),
            "api_key_set": bool(value.api_key),
            "local": value.is_local_process() or value.is_local_http(),
            "backend_kind": value.backend_kind(role),
            "device": value.base_url.removeprefix("local://") if value.is_local_process() else None,
            "thinking": value.thinking,
        }
        if role == "llm":
            payload["backend"] = backend_public_label(value) if value.base_url else ""
        return payload

    return {
        "main": safe(settings.main, role="llm"),
        "fast": safe(settings.fast, role="llm"),
        "embedding": safe(settings.embedding, role="embedding"),
        "setup_needed": not settings.main.is_ready(role="llm"),
        "override_path": str(override_path(settings)),
    }


def save_model_overrides(settings: Settings, updates: dict[str, dict[str, str]]) -> dict[str, Any]:
    path = override_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_model_overrides(path, settings.account_directory.name if settings.account_directory else None)
    patch: dict[str, Any] = {}
    for slot, values in updates.items():
        if slot not in SLOT_PATHS or not isinstance(values, dict):
            continue
        section: dict[str, str] = {}
        for field in ("backend", "device"):
            if values.get(field) is not None:
                section[field] = str(values[field]).strip()
        if values.get("model") is not None:
            section["model"] = str(values.get("model", "")).strip()
        if values.get("base_url") is not None:
            section["base_url"] = str(values.get("base_url", "")).strip().rstrip("/")
        if values.get("thinking") is not None:
            section["thinking"] = str(values["thinking"])
        api_key = values.get("api_key")
        if api_key is not None and str(api_key).strip():
            section["api_key"] = str(api_key).strip()
        if not section:
            continue
        cursor = patch
        keys = SLOT_PATHS[slot]
        for key in keys[:-1]:
            cursor = cursor.setdefault(key, {})
        cursor[keys[-1]] = section
    merged = _merge(existing, patch)
    for slot, keys in SLOT_PATHS.items():
        values = updates.get(slot, {})
        replacing_route = any(field in values and str(values[field]).strip() != str(getattr(getattr(settings, slot), field))
                              for field in ("model", "base_url", "backend"))
        replacing_key = bool(str(values.get("api_key") or "").strip())
        if replacing_route or replacing_key:
            section = merged
            for key in keys:
                section = section.get(key, {})
            # A locally selected replacement cannot silently keep a profile bound
            # to the old model/host. The key can instead come from an env reference.
            section.pop("credential_profile", None)
            if replacing_route and not replacing_key:
                section["api_key"] = ""
            if values.get("backend") in {"bge", "transformers"}:
                section.pop("base_url", None)
                section.pop("thinking", None)
            elif "base_url" in values and "thinking" not in values:
                section.pop("thinking", None)
    validated = copy.deepcopy(settings)
    apply_overrides(validated, merged)
    from .config_store import update_document
    def publish(document):
        target = document.setdefault('accounts', {}).setdefault(settings.account_directory.name, {}) if settings.account_directory else document
        for parent, slots in merged.items():
            target.setdefault(parent, {}).update(slots)
    settings.model_settings = update_document(path, publish)
    for slot in SLOT_PATHS:
        setattr(settings, slot, getattr(validated, slot))
    return public_models(settings)


async def test_model_slot(settings: Settings, slot: str) -> dict[str, Any]:
    if slot not in {"main", "fast", "embedding"}:
        raise ValueError("slot 必须是 main、fast 或 embedding")
    config: ModelConfig = getattr(settings, slot)
    role: SlotRole = "embedding" if slot == "embedding" else "llm"
    if slot == "embedding" and config.is_local_process():
        from .memory.embedding import EmbeddingClient, EmbeddingError
        client = EmbeddingClient(config, min(settings.request_timeout_seconds, 60), max_retries=0)
        try:
            vector = await client.embed_query("用户喜欢喝茶")
            if not vector:
                raise EmbeddingError("本地模型未配置或未返回向量")
            return {"ok": True, "slot": slot, "message": "本地 Embedding 编码正常",
                    "dimension": len(vector), "namespace": client.namespace}
        except EmbeddingError as exc:
            return {"ok": False, "slot": slot, "message": str(exc)}
    if role == "llm" and config.is_local_process():
        from .llm import LLMClient, ProviderError
        try:
            result = await LLMClient(settings).chat(
                [{"role": "user", "content": "ping"}],
                model=config,
                max_tokens=8,
            )
            return {
                "ok": True,
                "slot": slot,
                "message": "本地 LLM 推理正常",
                "preview": (result.content or "")[:80],
                "device": config.base_url.removeprefix("local://"),
            }
        except (ProviderError, RuntimeError, OSError, ImportError) as exc:
            return {"ok": False, "slot": slot, "message": str(exc)}
    if not config.is_ready(role=role):
        if role == "llm" and config.model and config.base_url and not config.api_key:
            return {"ok": False, "slot": slot, "message": "远程模型需要 API Key；本地可用 local://cuda 或 localhost OpenAI 兼容服务"}
        return {"ok": False, "slot": slot, "message": "模型未完整配置（需要 model、base_url；远程还需 api_key）"}
    headers = config.auth_headers()
    timeout = httpx.Timeout(min(settings.request_timeout_seconds, 30))
    async with httpx.AsyncClient(timeout=timeout, trust_env=not config.is_local_http()) as client:
        if slot == "embedding":
            response = await client.post(
                f"{config.base_url}/embeddings",
                headers=headers,
                json={"model": config.model, "input": ["memoria setup ping"], "encoding_format": "float"},
            )
        else:
            from .llm.backends.openai_http import OpenAICompatBackend
            response = await client.post(
                f"{config.base_url}/chat/completions",
                headers=headers,
                json=OpenAICompatBackend.payload(config, [{"role": "user", "content": "ping"}], 8),
            )
    if response.status_code >= 400:
        return {"ok": False, "slot": slot, "status_code": response.status_code, "message": "模型接口返回错误，请核对模型、端点、权限与配额"}
    try:
        body = response.json()
        if slot == "embedding":
            from .memory.embedding import EmbeddingClient
            vectors = EmbeddingClient._vectors(body, 1)
            details = {"dimension": len(vectors[0])}
        else:
            if not isinstance(body["choices"][0]["message"], dict):
                raise ValueError("invalid message")
            usage = {key: value for key, value in (body.get("usage") or {}).items()
                     if key in {"prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "reasoning_tokens"}
                     and type(value) is int and value >= 0}
            terminal = body["choices"][0].get("finish_reason")
            details = {"usage": usage or None, "finish_reason": terminal if terminal in {None,"stop","length","tool_calls","content_filter","function_call"} else "unknown",
                       "completed_generation": terminal == "stop"}
    except (ValueError, KeyError, TypeError, IndexError, RuntimeError, AttributeError):
        return {"ok": False, "slot": slot, "status_code": response.status_code, "message": "接口响应不符合所选模型协议；HTTP 200不等于有效模型结果"}
    message = "本地 OpenAI 兼容服务连通正常" if config.is_local_http() else "连通性正常"
    return {"ok": True, "slot": slot, "status_code": response.status_code, "message": message, **details}
