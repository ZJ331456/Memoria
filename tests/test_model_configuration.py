"""Model-file routing, credential boundaries and local layout migration, no inference."""
from __future__ import annotations

import copy
import asyncio
import json
import tomllib
from pathlib import Path

import pytest
import httpx

from memoria.config import ModelConfig, Settings
from memoria.config_layout import migrate_server_configuration
from memoria.models_config import apply_overrides, public_models, save_model_overrides


def system_config(tmp_path, models=None):
    source = tmp_path / 'config.toml'
    source.write_text('[storage]\ndatabase="' + (tmp_path / 'data/memoria.db').as_posix() + '"\n' +
                      ('[models]\nfile="models.toml"\n' if models is not None else ''), encoding='utf-8')
    if models is not None:
        (tmp_path / 'models.toml').write_text(models, encoding='utf-8')
    return source


def test_external_models_env_and_bge_are_explicit_and_root_stays_non_model(tmp_path, monkeypatch):
    monkeypatch.setenv('MEMORIA_CONFIG_TEST_KEY', 'test-provider-key')
    source = system_config(tmp_path, '''[llm.main]
backend="openai"
model="remote-fixture"
base_url="https://provider.example.test/v1"
api_key="${MEMORIA_CONFIG_TEST_KEY}"
[memory.embedding]
backend="bge"
model="model/bge-small-zh-v1.5"
device="cpu"
''')
    settings = Settings.load(source)
    assert settings.main.api_key == 'test-provider-key'
    assert settings.main.backend_kind('llm') == 'openai'
    assert settings.embedding.base_url == 'local://cpu'
    assert settings.embedding.is_ready(role='embedding')
    assert settings.preferred_chat_model(prefer_fast=True) is settings.main
    assert settings.private_config_directory == tmp_path / 'configs/local'
    assert not tomllib.loads(source.read_text()).get('llm')
    public = str(public_models(settings))
    assert 'test-provider-key' not in public and 'MEMORIA_CONFIG_TEST_KEY' not in public


def test_declaring_file_resolves_model_and_private_paths_under_extends(tmp_path):
    parent = tmp_path / 'parent'
    parent.mkdir()
    source = system_config(parent, '[llm.main]\nbackend="openai"\nmodel="local"\nbase_url="http://localhost:8080/v1"\n')
    source.write_text(source.read_text()+'[configuration]\nprivate_directory="private"\n')
    child = tmp_path / 'child.toml'
    child.write_text('extends="parent/config.toml"\n[agent]\nmax_tokens=123\n')
    settings = Settings.load(child)
    assert settings.model_override_file == parent / 'models.toml'
    assert settings.private_config_directory == parent / 'private'
    assert settings.max_tokens == 123


@pytest.mark.parametrize('text', [
    '[llm.main]\nbackend="bge"\nmodel="m"\ndevice="cpu"\n',
    '[memory.embedding]\nbackend="transformers"\nmodel="m"\ndevice="cpu"\n',
    '[memory.embedding]\nbackend="bge"\ndevice="gpu"\n',
    '[llm.main]\nbackend="openai"\nbase_url="local://cpu"\n',
    '[llm.main]\nbackend="openai"\nbase_url="https://secret@example.test/v1"\n',
    '[memory.embedding]\nbackend="openai"\nbase_url="https://example.test/v1"\ndevice="cuda"\n',
    '[memory.embedding]\nbackend="bge"\nmodel="m"\nthinking="enabled"\n',
    '[llm.main]\nbackend="openai"\nbase_url="https://example.test/v1"\nbase_urll="typo"\n',
])
def test_invalid_backend_role_or_fields_are_rejected_before_inference(tmp_path, text):
    with pytest.raises(ValueError):
        Settings.load(system_config(tmp_path, text))


def test_missing_explicit_model_file_is_not_silent_fallback(tmp_path):
    source = system_config(tmp_path, '')
    (tmp_path / 'models.toml').unlink()
    with pytest.raises(ValueError, match='missing'):
        Settings.load(source)


def test_account_override_cannot_inherit_cloud_key_or_thinking_on_other_endpoint(tmp_path):
    settings = Settings.load(system_config(tmp_path))
    settings.main = ModelConfig('cloud', 'cloud-fixture-key', 'https://api.deepseek.com/v1', 'disabled', backend='openai')
    apply_overrides(settings, {'llm': {'main': {'model': 'local', 'base_url': 'http://localhost:8080/v1'}}})
    assert settings.main.api_key == '' and settings.main.thinking is None
    assert settings.main.is_ready()
    assert settings.main.auth_headers()['Authorization'] == 'Bearer local'


def test_partial_invalid_override_does_not_change_any_slot(tmp_path):
    settings = Settings.load(system_config(tmp_path))
    before = copy.deepcopy(settings)
    with pytest.raises(ValueError):
        apply_overrides(settings, {'llm': {'main': {'model': 'changed'}}, 'memory': {'embedding': {'backend': 'wrong'}}})
    assert settings.main == before.main and settings.embedding == before.embedding


def test_failed_atomic_save_keeps_file_and_effective_models(tmp_path, monkeypatch):
    settings = Settings.load(system_config(tmp_path))
    save_model_overrides(settings, {'main': {'model': 'first', 'backend': 'openai', 'base_url': 'http://localhost:8080/v1'}})
    original = settings.model_override_file.read_bytes()
    before = copy.deepcopy(settings.main)
    def fail(*args):
        raise OSError('injected replacement failure')
    monkeypatch.setattr('memoria.models_config.os.replace', fail)
    with pytest.raises(OSError):
        save_model_overrides(settings, {'main': {'model': 'second'}})
    assert settings.model_override_file.read_bytes() == original
    assert settings.main == before
    assert not list(settings.model_override_file.parent.glob('.models-*.tmp'))


def test_save_endpoint_switch_does_not_persist_old_key(tmp_path):
    settings = Settings.load(system_config(tmp_path))
    save_model_overrides(settings, {'main': {'model': 'old', 'base_url': 'https://old.example.test/v1', 'api_key': 'old-fixture-key'}})
    save_model_overrides(settings, {'main': {'model': 'new', 'base_url': 'http://localhost:8080/v1'}})
    assert settings.main.api_key == ''
    assert Settings.load(settings.source).main.api_key == ''
    assert 'old-fixture-key' not in settings.model_override_file.read_text()


def test_bge_config_uses_same_runtime_namespace_without_http_or_importing_weights(tmp_path):
    from memoria.memory.embedding import EmbeddingClient
    model = tmp_path / 'bge-small-zh-v1.5'
    model.mkdir()
    (model / 'config.json').write_text('{"model_type":"bert","hidden_size":512}')
    legacy = EmbeddingClient(ModelConfig(str(model), '', 'local://cpu'))
    explicit = EmbeddingClient(ModelConfig(str(model), '', '', backend='bge', device='cpu'))
    assert legacy.namespace == explicit.namespace
    assert explicit.supports_document_chunks


def legacy_layout(tmp_path, monkeypatch):
    monkeypatch.setenv('CONFIG_MIGRATION_KEY', 'private-fixture-value')
    config = tmp_path / 'config.toml'
    config.write_text('''[llm]
provider="unused-legacy-selector"
request_timeout_seconds=37
[llm.main]
model="cloud"
base_url="https://api.example.test/v1"
api_key="${CONFIG_MIGRATION_KEY}"
[agent]
system_prompt="保持原始提示"
[server.security]
allowed_origins=["http://localhost:5173"]
[storage]
database="''' + (tmp_path / 'data/memoria.db').as_posix() + '"\n', encoding='utf-8')
    legacy = tmp_path / 'data/config'
    legacy.mkdir(parents=True)
    (legacy / 'models.override.toml').write_text('[memory.embedding]\nmodel="model/bge-small-zh-v1.5"\nbase_url="local://auto"\n')
    (legacy / 'feishu-bot.json').write_text('{"enabled":true}')
    (legacy / 'feishu-bot.credentials.dpapi').write_bytes(b'encrypted-fixture-only')
    (legacy / 'backups').mkdir()
    (legacy / 'backups/old.toml').write_text('archived=true')
    account = tmp_path / ('data/users/'+'a'*32+'/config/models.override.toml')
    account.parent.mkdir(parents=True)
    account.write_text('[llm.main]\nmodel="account-specific"\n')
    return config, legacy, account


def test_migration_preserves_non_model_values_env_and_account_files(tmp_path, monkeypatch):
    config, legacy, account = legacy_layout(tmp_path, monkeypatch)
    original, account_bytes = config.read_bytes(), account.read_bytes()
    result = migrate_server_configuration(config)
    assert result['account_files_changed'] == 0 and not legacy.exists()
    current = Settings.load(config)
    assert current.main.api_key == 'private-fixture-value'
    assert current.embedding.base_url == 'local://auto'
    assert current.system_prompt == '保持原始提示' and current.request_timeout_seconds == 37
    assert 'provider' not in tomllib.loads(config.read_text())['llm']
    models = current.model_override_file.read_text()
    assert '${CONFIG_MIGRATION_KEY}' in models and 'private-fixture-value' not in models
    assert (Path(result['backup']) / 'root-config.toml').read_bytes() == original
    assert (current.private_config_directory / 'feishu-bot.credentials.dpapi').read_bytes() == b'encrypted-fixture-only'
    assert account.read_bytes() == account_bytes
    assert migrate_server_configuration(config)['already_migrated'] is True


def test_migration_move_failure_restores_original_layout(tmp_path, monkeypatch):
    config, legacy, account = legacy_layout(tmp_path, monkeypatch)
    original = config.read_bytes()
    import shutil
    move = shutil.move
    def fail(start, destination):
        if str(start).endswith('feishu-bot.credentials.dpapi'):
            raise OSError('injected private file move failure')
        return move(start, destination)
    monkeypatch.setattr('memoria.config_layout.shutil.move', fail)
    with pytest.raises(OSError):
        migrate_server_configuration(config)
    assert config.read_bytes() == original
    assert (legacy / 'feishu-bot.json').is_file()
    assert (legacy / 'models.override.toml').is_file()
    assert account.is_file()
    assert not (tmp_path / 'configs/local/models.toml').exists()


def test_migration_refuses_unknown_files_or_existing_destination(tmp_path, monkeypatch):
    config, legacy, account = legacy_layout(tmp_path, monkeypatch)
    original = config.read_bytes()
    (legacy / 'unknown.private').write_text('keep')
    with pytest.raises(ValueError, match='Unknown'):
        migrate_server_configuration(config)
    assert config.read_bytes() == original and account.is_file()


def test_migration_does_not_replace_existing_private_model_file(tmp_path, monkeypatch):
    config, legacy, account = legacy_layout(tmp_path, monkeypatch)
    target = tmp_path / 'configs/local/models.toml'
    target.parent.mkdir(parents=True)
    target.write_text('[llm.main]\nmodel="keep-this"\n')
    original = config.read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        migrate_server_configuration(config)
    assert config.read_bytes() == original
    assert 'keep-this' in target.read_text() and (legacy / 'models.override.toml').is_file()


def test_unmigrated_legacy_layout_fails_clearly_instead_of_silent_model_switch(tmp_path, monkeypatch):
    config, legacy, account=legacy_layout(tmp_path,monkeypatch)
    before=config.read_bytes()
    with pytest.raises(ValueError,match='Legacy data/config'):
        Settings.load(config)
    assert config.read_bytes()==before and account.is_file()


@pytest.mark.parametrize('url,key', [('http://127.0.0.1:8080/v1',''),('https://provider.example.test/v1','fixture-key')])
def test_explicit_openai_llm_remote_and_loopback_use_same_protocol(tmp_path, url, key):
    from memoria.llm import LLMClient
    settings = Settings.load(system_config(tmp_path, f'[llm.main]\nbackend="openai"\nmodel="fixture"\nbase_url="{url}"\napi_key="{key}"\n'))
    def respond(request):
        assert str(request.url) == url + '/chat/completions'
        assert request.headers['authorization'] == 'Bearer ' + (key or 'local')
        body = json.loads(request.content)
        assert body['model'] == 'fixture' and 'thinking' not in body
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'pong'},'finish_reason':'stop'}]})
    result = asyncio.run(LLMClient(settings,transport=httpx.MockTransport(respond)).chat([{'role':'user','content':'ping'}],max_tokens=16))
    assert result.content == 'pong'


def test_explicit_api_embedding_sends_float_vectors_and_rejects_invalid_probe(tmp_path, monkeypatch):
    from memoria.memory.embedding import EmbeddingClient
    from memoria.models_config import test_model_slot
    settings = Settings.load(system_config(tmp_path, '[memory.embedding]\nbackend="openai"\nmodel="api-vector"\nbase_url="https://embedding.example.test/v1"\napi_key="fixture-key"\n'))
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        assert str(request.url).endswith('/v1/embeddings')
        assert request.headers['authorization'] == 'Bearer fixture-key'
        return httpx.Response(200,json={'data':[{'index':0,'embedding':[1.0,2.0]}]})
    real = httpx.AsyncClient
    monkeypatch.setattr('memoria.models_config.httpx.AsyncClient',lambda **kw:real(transport=httpx.MockTransport(respond),**kw))
    assert asyncio.run(EmbeddingClient(settings.embedding).embed_query('raw query')) == [1.0,2.0]
    assert requests[0] == {'model':'api-vector','input':['raw query'],'encoding_format':'float'}
    assert asyncio.run(test_model_slot(settings,'embedding'))['dimension'] == 2
    assert requests[1]['encoding_format'] == 'float'
    monkeypatch.setattr('memoria.models_config.httpx.AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'ok':True})),**kw))
    assert asyncio.run(test_model_slot(settings,'embedding'))['ok'] is False


def test_private_profile_is_account_bound_and_not_copied_to_global_file(tmp_path, monkeypatch):
    defaults = Settings.load(system_config(tmp_path))
    owner = 'b'*32
    account = copy.deepcopy(defaults)
    account.model_override_file = tmp_path/'data/users'/owner/'config/models.override.toml'
    account.model_override_file.parent.mkdir(parents=True)
    account.model_override_file.write_text('[llm.main]\nmodel="cloud"\nbase_url="https://api.deepseek.com/v1"\napi_key=""\ncredential_profile="private"\nthinking="disabled"\n')
    monkeypatch.setattr('memoria.models_config.load_private_json',lambda path:{'owner_user_id':owner,'model':'cloud','base_url':'https://api.deepseek.com/v1','api_key':'profile-fixture-key'})
    apply_overrides(account)
    assert account.main.api_key == 'profile-fixture-key' and defaults.main.api_key == ''
    save_model_overrides(account,{'main':{'thinking':'enabled','api_key':''}})
    assert 'credential_profile' in account.model_override_file.read_text()
    assert 'profile-fixture-key' not in account.model_override_file.read_text()
    assert not defaults.model_override_file.exists()
    assert account.main.thinking == 'enabled'


def test_migration_refuses_data_directory_escape(tmp_path):
    source=tmp_path/'config.toml'
    source.write_text('[storage]\ndatabase="../foreign/memoria.db"\n')
    before=source.read_bytes()
    with pytest.raises(ValueError,match='escapes'):
        migrate_server_configuration(source)
    assert source.read_bytes()==before
