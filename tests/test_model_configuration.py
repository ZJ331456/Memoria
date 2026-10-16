"""Central per-responsibility configuration, privacy, atomic saves and migration."""
import asyncio
import copy
import json
import tomllib
from pathlib import Path
import pytest
import httpx
from memoria.config import Settings, ModelConfig
from memoria.models_config import apply_overrides, apply_account_settings, public_models, save_model_overrides
from memoria.config_store import read_document, write_document
from memoria.config_layout import migrate_server_configuration


def system_config(tmp_path, models=None):
    directory=tmp_path/'config';directory.mkdir(exist_ok=True)
    source=directory/'system.toml'
    source.write_text('[storage]\ndatabase="'+(tmp_path/'data/memoria.db').as_posix()+'"\n[llm]\nrequest_timeout_seconds=37\n')
    (directory/'models.toml').write_text(models or '')
    (directory/'bot.toml').write_text('[feishu]\nenabled=false\n')
    (directory/'mcp.toml').write_text('')
    return source


def test_one_model_file_controls_remote_llm_and_local_bge_without_keys_in_public_output(tmp_path,monkeypatch):
    monkeypatch.setenv('MEMORIA_CONFIG_TEST_KEY','test-provider-key')
    source=system_config(tmp_path,'[llm.main]\nbackend="openai"\nmodel="remote"\nbase_url="https://provider.example.test/v1"\napi_key="${MEMORIA_CONFIG_TEST_KEY}"\n[memory.embedding]\nbackend="bge"\nmodel="model/bge-small-zh-v1.5"\ndevice="cpu"\n')
    settings=Settings.load(source)
    assert settings.main.api_key=='test-provider-key'
    assert settings.embedding.base_url=='local://cpu'
    assert settings.request_timeout_seconds==37
    assert settings.model_config_file==source.parent/'models.toml'
    assert 'test-provider-key' not in str(public_models(settings))
    assert 'main' not in read_document(source)['llm']


def test_incomplete_production_configuration_fails_without_silent_example_fallback(tmp_path):
    source=system_config(tmp_path)
    (source.parent/'models.toml').unlink()
    with pytest.raises(FileNotFoundError,match='incomplete'):Settings.load(source)
    with pytest.raises(FileNotFoundError):Settings.load(tmp_path/'missing.toml')


def test_old_split_reference_is_rejected_with_migration_instruction(tmp_path):
    source=tmp_path/'old.toml';source.write_text('[models]\nfile="models.toml"\n')
    with pytest.raises(ValueError,match='Split configuration'):Settings.load(source)


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
    original = settings.model_config_file.read_bytes()
    before = copy.deepcopy(settings.main)
    def fail(*args):
        raise OSError('injected replacement failure')
    monkeypatch.setattr('memoria.config_store.os.replace', fail)
    with pytest.raises(OSError):
        save_model_overrides(settings, {'main': {'model': 'second'}})
    assert settings.model_config_file.read_bytes() == original
    assert settings.main == before
    assert not list(settings.model_config_file.parent.glob('.config-*.tmp'))


def test_save_endpoint_switch_does_not_persist_old_key(tmp_path):
    settings = Settings.load(system_config(tmp_path))
    save_model_overrides(settings, {'main': {'model': 'old', 'base_url': 'https://old.example.test/v1', 'api_key': 'old-fixture-key'}})
    save_model_overrides(settings, {'main': {'model': 'new', 'base_url': 'http://localhost:8080/v1'}})
    assert settings.main.api_key == ''
    assert Settings.load(settings.source).main.api_key == ''
    assert 'old-fixture-key' not in settings.model_config_file.read_text()


def test_bge_config_uses_same_runtime_namespace_without_http_or_importing_weights(tmp_path):
    from memoria.memory.embedding import EmbeddingClient
    model = tmp_path / 'bge-small-zh-v1.5'
    model.mkdir()
    (model / 'config.json').write_text('{"model_type":"bert","hidden_size":512}')
    legacy = EmbeddingClient(ModelConfig(str(model), '', 'local://cpu'))
    explicit = EmbeddingClient(ModelConfig(str(model), '', '', backend='bge', device='cpu'))
    assert legacy.namespace == explicit.namespace
    assert explicit.supports_document_chunks


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



def test_account_model_sections_are_bound_to_uuid_and_preserve_other_sections_on_save(tmp_path,monkeypatch):
    source=system_config(tmp_path,'[llm.main]\nmodel="public-local"\nbase_url="http://localhost:8080/v1"\n')
    defaults=Settings.load(source);owner='b'*32
    document=read_document(defaults.model_config_file)
    document['accounts']={owner:{'llm':{'main':{'backend':'openai','model':'cloud','base_url':'https://api.deepseek.com/v1','api_key':'','credential_profile':'private','thinking':'disabled'}}}}
    write_document(defaults.model_config_file,document)
    defaults=Settings.load(source)
    monkeypatch.setattr('memoria.models_config.load_private_json',lambda p:{'owner_user_id':owner,'model':'cloud','base_url':'https://api.deepseek.com/v1','api_key':'private-fixture-key'})
    account=apply_account_settings(copy.deepcopy(defaults),tmp_path/'data/users'/owner)
    other=apply_account_settings(copy.deepcopy(defaults),tmp_path/'data/users'/('c'*32))
    assert account.main.api_key=='private-fixture-key' and not other.main.api_key
    save_model_overrides(account,{'main':{'thinking':'enabled','api_key':''}})
    assert 'private-fixture-key' not in account.model_config_file.read_text()
    result=Settings.load(source)
    assert result.main.model=='public-local'
    assert read_document(source)['llm']['request_timeout_seconds']==37
    assert apply_account_settings(result,tmp_path/'data/users'/owner).main.thinking=='enabled'
    apply_account_settings(result,tmp_path/'data/users'/('c'*32))
    assert result.main.model=='public-local' and not result.main.api_key
    with pytest.raises(ValueError,match='UUID'):apply_account_settings(result,tmp_path/'foreign'/owner)


def test_toml_roundtrip_preserves_parent_scalars_nested_tables_arrays_and_quoted_names(tmp_path):
    data={'agent':{'max_tokens':777,'context':{'allowed':['a','b'],'empty':[]}},'accounts':{'a.b':{'mcp':{'enabled_servers':['search']}}}}
    path=tmp_path/'roundtrip.toml';write_document(path,data)
    assert read_document(path)==data


def test_validated_profile_activation_only_archives_and_updates_its_account(tmp_path,monkeypatch):
    from memoria.experiments.history import HistoryWorkspace
    from memoria.experiments.runner import activate_profile
    owner,other='a'*32,'b'*32
    source=system_config(tmp_path)
    settings=Settings.load(source)
    document={'llm':{'main':{'model':'public-local','base_url':'http://localhost:8080/v1'}},
              'accounts':{owner:{'memory':{'embedding':{'backend':'bge','model':'model/bge-small-zh-v1.5'}}},
                          other:{'llm':{'main':{'model':'other-model','api_key':'other-fixture-secret'}}}}}
    write_document(settings.model_config_file,document)
    settings=Settings.load(source)
    workspace=HistoryWorkspace(tmp_path/'data/users'/owner)
    saved={'owner_user_id':owner,'model':'validated-cloud','base_url':'https://api.deepseek.com/v1','api_key':'own-fixture-key','thinking':'disabled'}
    monkeypatch.setattr('memoria.experiments.runner.load_private_json',lambda p:saved)
    monkeypatch.setattr('memoria.models_config.load_private_json',lambda p:saved)
    monkeypatch.setattr('memoria.experiments.runner.load_run',lambda *a:(None,{'protocol':{'mode':'extract','extraction_protocol':'source_grounded'},
        'models':{'extractor':{'model':saved['model'],'endpoint':saved['base_url']}}},[{'status':'success','candidates':[{}]}]))
    output=activate_profile(workspace,settings,'profile','engineering-run')
    after=read_document(output)
    assert after['llm']==document['llm'] and after['accounts'][other]==document['accounts'][other]
    assert after['accounts'][owner]['memory']==document['accounts'][owner]['memory']
    assert 'own-fixture-key' not in output.read_text()
    archived=(workspace.root/'model-activations/engineering-run.previous.toml').read_text()
    assert 'other-fixture-secret' not in archived
    assert (workspace.root/'model-activation.json').is_file() and not (workspace.directory/'config').exists()


def test_mcp_central_selection_reload_and_account_owned_secret_paths(tmp_path):
    from memoria.mcp.config import configured_mcp_servers
    source=system_config(tmp_path)
    owner='a'*32
    settings=Settings.load(source)
    write_document(settings.mcp_config_file,{'servers':{'feishu':{
        'command':['{project}/bridge','feishu'],'cwd':'{project}',
        'env':{'PRIVATE':'{account_dir}/secrets/token'},'allowed_tools':['read'],'risk':'read-only'}},
        'accounts':{owner:{'mcp':{'enabled_servers':['feishu']}}}})
    assert configured_mcp_servers(settings)==[]
    account=apply_account_settings(copy.deepcopy(settings),tmp_path/'data/users'/owner)
    server=configured_mcp_servers(account)[0]
    assert server.enabled and server.env['PRIVATE']==str(account.account_directory)+'/secrets/token'
    assert server.public_dict()['env_keys']==['PRIVATE'] and 'env' not in server.public_dict()
    other=apply_account_settings(copy.deepcopy(settings),tmp_path/'data/users'/('b'*32))
    assert not configured_mcp_servers(other)[0].enabled
    document=read_document(settings.mcp_config_file)
    document['accounts'][owner]['mcp']['enabled_servers']=[]
    write_document(settings.mcp_config_file,document)
    assert not configured_mcp_servers(account)[0].enabled
    document['accounts'][owner]['mcp']['enabled_servers']=['missing']
    write_document(settings.mcp_config_file,document)
    with pytest.raises(ValueError,match='no definition'):configured_mcp_servers(account)


def legacy_layout(tmp_path):
    source=tmp_path/'config.toml'
    source.write_text('[llm]\nrequest_timeout_seconds=37\n[llm.main]\nmodel="cloud"\nbase_url="https://api.example.test/v1"\napi_key="${MIGRATION_FIXTURE_KEY}"\n[storage]\ndatabase="'+(tmp_path/'data/memoria.db').as_posix()+'"\n')
    legacy=tmp_path/'configs/local';legacy.mkdir(parents=True)
    (legacy/'feishu-bot.json').write_text('{"enabled":true}')
    (legacy/'feishu-bot.credentials.dpapi').write_bytes(b'encrypted-fixture-only')
    account=tmp_path/'data/users'/('a'*32)/'config';account.mkdir(parents=True)
    (account/'models.override.toml').write_text('[llm.main]\nmodel="account-model"\n')
    (account/'mcp_servers.json').write_text('{"servers":[{"name":"search","command":["runtime"],"enabled":true}]}')
    return source,legacy,account


def test_migration_preserves_secrets_env_models_account_selection_and_archives_old_files(tmp_path,monkeypatch):
    monkeypatch.setenv('MIGRATION_FIXTURE_KEY','private-fixture-value')
    source,legacy,account=legacy_layout(tmp_path);original=source.read_bytes()
    result=migrate_server_configuration(source)
    assert not source.exists() and not legacy.exists()
    current=Settings.load(tmp_path/'config/system.toml')
    assert current.main.api_key=='private-fixture-value' and current.request_timeout_seconds==37
    assert 'private-fixture-value' not in current.model_config_file.read_text()
    assert (tmp_path/'data/secrets/feishu-bot.credentials.dpapi').read_bytes()==b'encrypted-fixture-only'
    assert (Path(result['backup'])/'originals/config.toml').read_bytes()==original
    assert not (account/'models.override.toml').exists() and not (account/'mcp_servers.json').exists()
    configured=apply_account_settings(current,account.parent)
    from memoria.mcp.config import configured_mcp_servers
    servers=configured_mcp_servers(configured)
    assert next(s for s in servers if s.name=='search').command==('runtime',)
    assert next(s for s in servers if s.name=='search').enabled
    assert migrate_server_configuration(source)['already_migrated']


def test_failed_migration_restores_originals_and_removes_published_files(tmp_path,monkeypatch):
    source,legacy,account=legacy_layout(tmp_path);original=source.read_bytes()
    import shutil
    real=shutil.move
    def fail(start,destination):
        if str(start).endswith('feishu-bot.credentials.dpapi'):raise OSError('injected move failure')
        return real(start,destination)
    monkeypatch.setattr('memoria.config_layout.shutil.move',fail)
    with pytest.raises(OSError):migrate_server_configuration(source)
    assert source.read_bytes()==original
    assert (legacy/'feishu-bot.credentials.dpapi').is_file() and (account/'models.override.toml').is_file()
    assert not any((tmp_path/'config').glob('*.toml'))


def test_migration_refuses_existing_targets_and_paths_outside_project(tmp_path):
    source,legacy,account=legacy_layout(tmp_path)
    target=tmp_path/'config/models.toml';target.parent.mkdir();target.write_text('keep=true')
    with pytest.raises(ValueError,match='already exists'):migrate_server_configuration(source)
    assert target.read_text()=='keep=true'
    target.unlink()
    source.write_text('[storage]\ndatabase="../foreign/memoria.db"\n')
    with pytest.raises(ValueError,match='escapes'):migrate_server_configuration(source)


@pytest.mark.parametrize('private', ['.', 'config', 'data', 'data/users'])
def test_migration_cannot_retire_project_config_or_account_roots(tmp_path,private):
    source,legacy,account=legacy_layout(tmp_path)
    source.write_text(source.read_text()+'[configuration]\nprivate_directory="'+private+'"\n')
    original=source.read_bytes()
    with pytest.raises(ValueError,match='overlaps'):migrate_server_configuration(source)
    assert source.read_bytes()==original and account.is_dir()


def test_previous_feishu_file_moves_into_unified_bot_without_touching_models_or_accounts(tmp_path):
    source=system_config(tmp_path)
    directory=source.parent
    (directory/'bot.toml').unlink()
    old=directory/'feishu.toml';old.write_text('[bot]\nenabled=true\ncredentials_file="data/secrets/existing.dpapi"\n')
    models=(directory/'models.toml').read_bytes()
    result=migrate_server_configuration(source)
    assert result['account_files_changed']==0 and not old.exists()
    current=Settings.load(source)
    assert current.bot_settings['feishu']['enabled']
    assert not current.bot_settings['qq']['enabled'] and not current.bot_settings['telegram']['enabled']
    assert (directory/'models.toml').read_bytes()==models
    assert (Path(result['backup'])/'feishu.toml').is_file()
    assert migrate_server_configuration(source)['already_migrated']
