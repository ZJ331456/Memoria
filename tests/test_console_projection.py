"""Isolated engineering sources, not personal history or benchmark data."""
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from fastapi.testclient import TestClient
from memoria.api import create_app


@pytest.fixture
def console(tmp_path):
    config=tmp_path/'config.toml'
    config.write_text(f'[storage]\ndatabase="{(tmp_path/"legacy.db").as_posix()}"\n[agent.mcp]\nenabled=false\n[memory.markdown]\nenabled=false\n')
    app=create_app(config)
    with TestClient(app) as client:
        creds={'username':'alice','password':'Console-engineering-password'}
        client.post('/api/auth/register',json=creds)
        identity=client.post('/api/auth/login',json=creds).json()
        alice={'Authorization':'Bearer '+identity['access_token']}
        assert client.get('/api/studio/summary',headers=alice).status_code==200
        tenant=app.state.tenants.apps[identity['user']['id']]
        store=tenant.state.store
        first=store.create_session('Long isolated source')['id'];second=store.create_session('Other source')['id']
        with store.lock,store.db:
            store.db.executemany('INSERT INTO messages(id,session_id,role,content,created_at) VALUES(?,?,?,?,?)',
                [(f'm-{i}',first,'user',('needle %_\\ literal 🙂' if i==1104 else f'Original fixture line {i}'),'2026-01-01T00:00:00Z') for i in range(1105)])
        store.add_message(second,'user','An unrelated private fixture sentence')
        receipt={'source_mapping':[{'session_id':first,'source_id':'public-source-1','message_ids':[]}]}
        with store.db:
            store.db.execute('INSERT INTO dataset_imports VALUES(?,?,?,?,?)',('engineering-source','0'*64,'{}',json.dumps(receipt),'2026-01-01T00:00:00Z'))
        creds['username']='bob';client.post('/api/auth/register',json=creds)
        bob_identity=client.post('/api/auth/login',json=creds).json()
        bob={'Authorization':'Bearer '+bob_identity['access_token']}
        yield client,store,alice,bob,first,second


def test_console_counts_and_sources_are_real_tenant_scoped(console):
    client,store,alice,bob,first,second=console
    result=client.get('/api/studio/summary',headers=alice).json()
    assert result['sessions']==2 and result['messages']==1106 and result['memories']==0
    assert result['claims']==0 and result['datasets']==1 and not result['model_called']
    identity=client.get('/api/auth/me',headers=alice).json()
    assert client.get('/api/studio/summary',headers={**alice,'X-Memoria-User':'forged'}).headers['X-Memoria-User']==identity['id']
    assert client.get('/api/studio/summary',headers=bob).json()['sessions']==0
    assert client.get('/api/studio/conversations',headers=bob).json()['items']==[]
    assert client.get(f'/api/studio/conversations/{first}/messages',headers=bob).status_code==404
    client.cookies.clear()
    assert client.get('/api/studio/conversations').status_code==401
    for entry in ('/', '/studio/'):
        shell=client.get(entry, headers={'Accept':'text/html'})
        assert shell.status_code in {200,503}
        assert "frame-ancestors 'none'" in shell.headers['Content-Security-Policy']


def test_title_full_text_literal_search_origin_and_bounded_pagination(console):
    client,_,alice,_,first,second=console
    response=client.get('/api/studio/conversations',params={'q':'%_\\'},headers=alice)
    assert response.status_code==200
    result=response.json();assert result['total']==1 and result['items'][0]['id']==first
    assert result['items'][0]['preview_message_id']=='m-1104' and 'needle' in result['items'][0]['preview']
    assert result['items'][0]['matching_messages']==1
    assert result['items'][0]['origin']['dataset_id']=='engineering-source'
    assert client.get('/api/studio/conversations',params={'origin':'native'},headers=alice).json()['items'][0]['id']==second
    assert client.get('/api/studio/conversations',params={'origin':'imported'},headers=alice).json()['total']==1
    assert client.get('/api/studio/conversations',params={'q':'Long isolated'},headers=alice).json()['total']==1
    assert client.get('/api/studio/conversations',params={'page':99,'page_size':1},headers=alice).json()['page']==2
    assert client.get('/api/studio/conversations',params={'page_size':1000},headers=alice).status_code==422


def test_complete_history_beyond_old_cap_and_search_anchor_integrity(console):
    client,_,alice,_,first,second=console
    result=client.get(f'/api/studio/conversations/{first}/messages',params={'page':23,'page_size':50},headers=alice).json()
    assert result['total']==1105 and len(result['items'])==5
    assert result['items'][0]['ordinal']==1101 and result['items'][-1]['id']=='m-1104'
    result=client.get(f'/api/studio/conversations/{first}/messages',params={'anchor_id':'m-1104','q':'needle'},headers=alice).json()
    assert result['page']==23 and result['matched_total']==1 and result['matches'][0]['ordinal']==1105
    assert result['items'][-1]['content']=='needle %_\\ literal 🙂'
    assert result['session']['origin']['kind']=='imported'
    assert client.get(f'/api/studio/conversations/{second}/messages',params={'anchor_id':'m-1104'},headers=alice).status_code==404


def test_memory_inventory_literal_read_only_no_embedding_and_cross_user(console):
    client,store,alice,bob,_,_=console
    memory=store.add_memory('Literal 100%_ evidence, not generated','fact')
    with store.db:store.db.execute('UPDATE memories SET embedding=? WHERE id=?',('[1,2,3]',memory['id']))
    result=client.get('/api/studio/memories',params={'q':'100%_'},headers=alice).json()
    assert result['total']==1 and result['items'][0]['id']==memory['id']
    assert 'embedding' not in result['items'][0] and result['model_called'] is False
    assert client.get('/api/studio/memories',headers=bob).json()['total']==0
    schema=client.get('/openapi.json').json()
    assert schema['paths']['/api/studio/conversations']['get']['security']==[{'UserSession':[]}]
    root=client.get('/',headers={'Accept':'text/html'},follow_redirects=False)
    assert root.status_code in {200,503} and 'location' not in root.headers
    if root.status_code==200:
        assert '<title>Memoria · 对话</title>' in root.text
        assert 'chat-' in root.text and 'studio-' not in root.text
        assert 'Memoria Studio' in client.get('/studio/').text
    assert client.get('/').json()['chat']=='/'
    assert client.get('/app/').status_code==404


def test_cjk_readability_search_never_mutates_original_or_english_spacing(console):
    client,store,alice,_,first,_=console
    text='我 喜 欢 中 文 ， 但 是 English words stay separate\n下 一 行 1 8'
    item=store.add_message(first,'user',text)
    response=client.get('/api/studio/conversations',params={'q':'喜欢中文'},headers=alice).json()
    assert response['total']==1 and response['items'][0]['preview_message_id']==item['id']
    packet=client.get(f'/api/studio/conversations/{first}/messages',params={'q':'喜欢中文','anchor_id':item['id']},headers=alice).json()
    actual=next(row for row in packet['items'] if row['id']==item['id'])
    assert actual['content']==text and '我喜欢中文，但是 English words stay separate\n下一行 1 8'==actual['display_content']
    assert packet['matched_total']==1
    assert client.get('/api/studio/conversations',params={'q':'Englishwords'},headers=alice).json()['total']==0
    assert store.message_source(item['id'])['content']==text


def test_authenticated_chat_stream_saves_only_the_current_users_messages(console,monkeypatch):
    client,store,alice,bob,_,session=console
    owner=client.get('/api/auth/me',headers=alice).json()['id']
    service=client.app.state.tenants.apps[owner].state.service
    async def engineering_turn(session_id,content,on_event=None,request_id=None):
        store.add_message(session_id,'user',content)
        await on_event({'type':'delta','content':'隔离测试🙂'})
        message=store.add_message(session_id,'assistant','隔离测试🙂')
        trace=store.add_trace(session_id,'completed',1,1,[],[])
        return message,[],trace
    monkeypatch.setattr(service,'chat_with_trace',engineering_turn)
    response=client.post(f'/api/sessions/{session}/chat/stream',json={'content':'工程输入'},headers=alice)
    events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert response.status_code==200 and response.headers['X-Memoria-User']==owner
    assert [event['type'] for event in events]==['delta','complete']
    assert events[-1]['message']['content']=='隔离测试🙂'
    assert client.post(f'/api/sessions/{session}/chat/stream',json={'content':'forbidden'},headers=bob).status_code==404
    packet=client.get(f'/api/studio/conversations/{session}/messages',headers=alice).json()
    assert [item['content'] for item in packet['items']][-2:]==['工程输入','隔离测试🙂']


def test_chat_cancel_closes_active_stream_without_generating_a_reply(console,monkeypatch):
    client,store,alice,_,_,session=console
    owner=client.get('/api/auth/me',headers=alice).json()['id']
    service=client.app.state.tenants.apps[owner].state.service
    started=Event()
    async def engineering_wait(session_id,content,on_event=None,request_id=None):
        started.set()
        await asyncio.sleep(20)
        raise AssertionError('A cancelled engineering turn must not complete')
    monkeypatch.setattr(service,'chat_with_trace',engineering_wait)
    before=len(store.messages(session))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(client.post,f'/api/sessions/{session}/chat/stream',json={'content':'cancel engineering only'},headers=alice)
        assert started.wait(5)
        assert client.post(f'/api/sessions/{session}/cancel',json={},headers=alice).json()['status']=='cancelled'
        response=future.result(timeout=5)
    assert '"type": "cancelled"' in response.text and '"type": "complete"' not in response.text
    assert len(store.messages(session))==before
