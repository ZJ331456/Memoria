import { test } from 'node:test'
import assert from 'node:assert/strict'
import { API, readEvents, SessionChanged } from '../shared/api.ts'

test('SSE preserves UTF-8 and CRLF boundaries across chunks',async()=>{
  const data=new TextEncoder().encode('data: {"type":"delta","content":"你好🙂"}\r\n\r\ndata: {"type":"complete"}\n\n')
  const stream=new ReadableStream({start(controller){for(let i=0;i<data.length;i+=2)controller.enqueue(data.slice(i,i+2));controller.close()}})
  const events:any[]=[]
  await readEvents(new Response(stream),()=>{},event=>events.push(event))
  assert.deepEqual(events,[{type:'delta',content:'你好🙂'},{type:'complete'}])
})

test('reset during SSE drops buffered private events and cancels the reader',async()=>{
  const previous=globalThis.fetch;let cancelled=false;let pushes=0
  const stream=new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('data: {"type":"delta","content":"old"}\n\ndata: {"type":"complete","private":"old"}\n\n'))},cancel(){cancelled=true}})
  globalThis.fetch=async()=>new Response(stream,{headers:{'X-Memoria-User':'alice'}})
  try{const api=new API();api.bindUser('alice');await assert.rejects(api.stream('/test',{},()=>{pushes++;api.reset()}),SessionChanged);assert.equal(pushes,1);assert.equal(cancelled,true)}finally{globalThis.fetch=previous}
})

test('SSE owner mismatch is rejected before any event reaches the UI',async()=>{
  const previous=globalThis.fetch
  globalThis.fetch=async()=>new Response('data: {"private":"bob"}\n\n',{headers:{'X-Memoria-User':'bob'}})
  try{const api=new API();api.bindUser('alice');let cleared=false;api.onUnauthorized=()=>{cleared=true;api.reset()};await assert.rejects(api.stream('/test',{},()=>assert.fail('foreign event rendered')),SessionChanged);assert.equal(cleared,true)}finally{globalThis.fetch=previous}
})
