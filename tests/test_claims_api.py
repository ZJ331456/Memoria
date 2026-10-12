"""Authenticated contracts and prompt integrity, using temporary engineering data."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.api.app import _create_tenant_app
from memoria.claims import ClaimDecision, ClaimDraft, ViewRequest, sha
from memoria.llm import ChatResult
from memoria.memory.budget import TurnMemoryBudget
from memoria.prompting import ContextBudget, PromptAssembler, PromptSection
from memoria.prompting.assembler import compact_context_frame


def credentials(client,username):
    body={"username":username,"password":"Engineering-passphrase-2026"}
    assert client.post("/api/auth/register",json=body).status_code==201
    response=client.post("/api/auth/login",json=body).json()
    return response["user"],{"Authorization":"Bearer "+response["access_token"]}


def test_claim_api_authentication_ownership_source_validation_and_openapi(tmp_path):
    config=tmp_path/"config.toml"
    config.write_text(f'[storage]\ndatabase="{(tmp_path/"legacy.db").as_posix()}"\n[agent.mcp]\nenabled=false\n[memory.markdown]\nenabled=false\n')
    app=create_app(config)
    with TestClient(app) as client:
        assert client.get("/api/claims").status_code==401
        user,q=credentials(client,"qin")
        _,a=credentials(client,"alice")
        sid=client.post("/api/sessions",json={"title":"Engineering source"},headers=q).json()["id"]
        tenant=app.state.tenants.apps[user["id"]]
        message=tenant.state.store.add_message(sid,"user","Engineering original: 中文")
        evidence={"message_id":message["id"],"start":0,"end":len(message["content"]),"quote":message["content"],"content_sha256":sha(message["content"])}
        body={"predicate":"response_language","value":"中文","evidence":[evidence]}
        assert client.post("/api/claims",json={**body,"user_id":user["id"]},headers=q).status_code==422
        assert client.post("/api/claims",json=body,headers=a).status_code==422
        response=client.post("/api/claims",json=body,headers=q)
        assert response.status_code==201,response.text
        candidate=response.json()
        cid=candidate["id"]
        assert candidate["user_id"]==user["id"]
        decision={"expected_revision":1,"reason":"工程明确批准"}
        assert client.post(f"/api/claims/{cid}/approve",json=decision,headers=a).status_code==404
        assert client.post(f"/api/claims/{cid}/approve",json=decision,headers=q).json()["status"]=="approved"
        assert client.post(f"/api/claims/{cid}/approve",json=decision,headers=q).status_code==409
        assert client.get("/api/claims",headers=a).json()==[]
        for suffix in ("","/history"):
            assert client.get(f"/api/claims/{cid}"+suffix,headers=a).status_code==404
        view=client.post("/api/claim-views",json={"predicates":["response_language"]},headers=q).json()
        assert view["valid"]
        assert client.get("/api/claim-views/"+view["id"],headers=a).status_code==404
        result=client.post("/api/memory-access",json={"predicates":["response_language"]},headers=q).json()
        assert result["path"]=="view" and result["payload"]["claims"][0]["value"]=="中文"
        assert client.post("/api/memory-access",json={"predicates":["response_language"]},headers=a).json()["path"]=="clarify"
        assert client.post("/api/memory-access",json={"predicates":["response_language"],"user_id":user["id"]},headers=a).status_code==422
        assert client.delete(f"/api/claims/{cid}",headers=a).status_code==404
        assert client.post(f"/api/claims/{cid}/dispute",json={"expected_revision":2,"reason":"争议"},headers=q).status_code==200
        assert not client.get("/api/claim-views/"+view["id"],headers=q).json()["valid"]
        assert client.post(f"/api/claims/{cid}/restore",json={"expected_revision":3,"reason":"明确恢复"},headers=q).status_code==200
        assert client.delete(f"/api/claims/{cid}",headers=q).status_code==204
        async def empty_extraction(*args,**kwargs):
            return ChatResult(content='{"claims":[]}',raw_message={"content":'{"claims":[]}'})
        tenant.state.service.llm.chat=empty_extraction
        response=client.post("/api/claims/extract",json={"message_id":message["id"]},headers=q)
        assert response.status_code==201 and response.json()["candidates"]==[]
        schema=client.get("/openapi.json").json()
        assert schema["paths"]["/api/memory-access"]["post"]["security"]==[{"UserSession":[]}]
        assert schema["paths"]["/api/claim-views"]["post"]["requestBody"]
    assert not (tmp_path/"legacy.db").exists()


def test_state_runtime_uses_claims_and_cache_without_planner_or_generic_retrieval(tmp_path):
    config=tmp_path/"config.toml"
    config.write_text(f'[storage]\ndatabase="{(tmp_path/"runtime.db").as_posix()}"\n[agent.mcp]\nenabled=false\n[memory.markdown]\nenabled=false\n')
    app=_create_tenant_app(config)
    store,service=app.state.store,app.state.service
    session=store.create_session("Engineering runtime")
    message=store.add_message(session["id"],"user","Engineering residence A")
    e={"message_id":message["id"],"start":0,"end":len(message["content"]),"quote":message["content"],"content_sha256":sha(message["content"])}
    candidate=service.claims.create(ClaimDraft(predicate="residence",value="A",evidence=[e]))
    service.claims.approve(candidate["id"],ClaimDecision(expected_revision=1,reason="工程批准"))
    seen=[]
    async def forbidden(*_args,**_kwargs):raise AssertionError("structured route must skip this model/retrieval")
    async def reader(messages,**_kwargs):
        seen.append(messages)
        return ChatResult(content="Engineering response",raw_message={"content":"Engineering response"})
    service.runtime.retrieval_planner.plan=forbidden
    service.runtime.memory.retrieve=forbidden
    service.runtime.compactor.ensure=forbidden
    service.runtime.llm.chat=reader
    try:
        for expected in ("retrieve","view"):
            _,memories,trace=asyncio.run(service.runtime.run(session["id"],"我现在住在哪里？",skip_memory_enqueue=True))
            assert memories==[] and trace["metadata"]["claim_access"]["path"]==expected
            assert "Structured State" in seen[-1][1]["content"] and message["id"] in seen[-1][1]["content"]
            assert trace["metadata"]["stage_timings"]["stages"]["claim.access"]["calls"]==1
    finally:store.close()


def test_structured_context_is_whole_or_omitted_never_partially_clipped():
    text=json.dumps({"value":"Engineering value","scope":{"travel":True},"quote":"evidence"*400},ensure_ascii=False)
    frame=PromptAssembler(max_frame_chars=500).assemble("rules",extra_sections=[PromptSection("Structured State",text,10,atomic=True)])
    content=frame.context_frame["content"]
    assert "Engineering value" not in content and "不得" in content
    full=PromptAssembler().assemble("rules",extra_sections=[PromptSection("Structured State",text,10,atomic=True)])
    for shortened in (compact_context_frame(full.context_frame["content"],500),
                      ContextBudget(650).apply(full.as_messages()+[{"role":"user","content":"probe"}]).messages[1]["content"]):
        assert "Engineering value" not in shortened and "不得" in shortened
    budget=TurnMemoryBudget(1,80)
    assert budget.allow_call("access_claims") and not budget.allow_call("access_claims")
    assert "用尽" in budget.consume(text,atomic=True)
    tool={"role":"tool","tool_call_id":"case-1","content":"[claim-access-atomic]\n"+text}
    messages=[{"role":"system","content":"rules"},{"role":"user","content":"probe"},
              {"role":"assistant","content":None,"tool_calls":[{"id":"case-1","type":"function","function":{"name":"access_claims","arguments":"{}"}}]},tool]
    fitted=ContextBudget(1000,tool_result_chars=100).apply(messages)
    tool_result=next(m["content"] for m in fitted.messages if m["role"]=="tool")
    assert "Engineering value" not in tool_result and "不得" in tool_result
