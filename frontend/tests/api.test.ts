import { test } from 'node:test'
import assert from 'node:assert/strict'
import { API, SessionChanged, sourceParts } from '../shared/api.ts'
import { textParts,readableText } from '../shared/console.ts'

test('source spans use codepoints, including astral characters', () => {
  assert.deepEqual(sourceParts('我🙂用中文', 2, 5), ['我🙂', '用中文', ''])
  assert.throws(() => sourceParts('短', 0, 3))
})

test('logout/reset prevents delayed response from leaking across accounts', async () => {
  const previous = globalThis.fetch
  let release!: (r: Response) => void
  globalThis.fetch = () => new Promise(resolve => { release = resolve })
  try {
    const api = new API()
    const pending = api.request('/claims')
    api.reset()
    release(new Response(JSON.stringify([{private:'old-account'}]), {status:200, headers:{'Content-Type':'application/json'}}))
    await assert.rejects(pending, SessionChanged)
  } finally { globalThis.fetch = previous }
})

test('401 clears private UI while login failures remain actionable', async () => {
  const previous = globalThis.fetch
  globalThis.fetch = async () => new Response('{"message":"用户名或密码错误"}', {status:401})
  try {
    const api = new API(); let cleared = false
    api.onUnauthorized = () => { cleared = true; api.reset() }
    await assert.rejects(api.request('/auth/login', {}), /用户名或密码错误/)
    assert.equal(cleared, false)
    await assert.rejects(api.request('/claims'), SessionChanged)
    assert.equal(cleared, true)
  } finally { globalThis.fetch = previous }
})

test('a late conversation response cannot replace the newly selected conversation', async () => {
  const previous=globalThis.fetch
  const release:((r:Response)=>void)[]=[]
  globalThis.fetch=()=>new Promise(resolve=>release.push(resolve))
  try {
    const api=new API()
    const old=api.request('/studio/conversations/old/messages',undefined,'GET','conversation')
    const next=api.request('/studio/conversations/new/messages',undefined,'GET','conversation')
    release[1](new Response('{"items":["new"]}',{status:200}))
    assert.deepEqual(await next,{items:['new']})
    release[0](new Response('{"items":["private old"]}',{status:200}))
    await assert.rejects(old,SessionChanged)
  } finally {globalThis.fetch=previous}
})

test('search highlight is literal text and preserves original content',()=>{
  for(const query of ['[a+b]','🙂','<script>','%_']){
    const text='Prefix '+query+' suffix '+query
    const parts=textParts(text,query)
    assert.equal(parts.map(p=>p.text).join(''),text)
    assert.equal(parts.filter(p=>p.hit).length,2)
  }
})

test('readable Chinese projection preserves English word boundaries, numbers and line breaks',()=>{
  assert.equal(readableText('我 喜 欢 中 文 ， 但 是 English words stay separate\n下 一 行 1 8'),'我喜欢中文，但是 English words stay separate\n下一行 1 8')
})

test('another tab changing the cookie cannot render a different owners response under the old identity',async()=>{
  const previous=globalThis.fetch
  globalThis.fetch=async()=>new Response('{"private":"other owner"}',{status:200,headers:{'X-Memoria-User':'other-owner'}})
  try{const api=new API();api.bindUser('current-owner');let cleared=false;api.onUnauthorized=()=>{cleared=true;api.reset()};await assert.rejects(api.request('/studio/summary'),SessionChanged);assert.equal(cleared,true)}finally{globalThis.fetch=previous}
})

test('a stalled data read times out visibly and the next request can recover',async()=>{
  const previous=globalThis.fetch
  globalThis.fetch=async(_input,init)=>new Promise((_resolve,reject)=>{
    init?.signal?.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true})
  })
  try {
    const api=new API(15)
    await assert.rejects(api.request('/studio/summary'),/读取超时/)
    globalThis.fetch=async()=>new Response('{"sessions":53}',{status:200})
    assert.deepEqual(await api.request('/studio/summary'),{sessions:53})
  } finally {globalThis.fetch=previous}
})

test('a wrong current password remains an actionable error and keeps the account signed in',async()=>{
  const previous=globalThis.fetch
  globalThis.fetch=async()=>new Response('{"detail":"当前密码不正确"}',{status:400})
  try {
    const api=new API();let cleared=false;api.onUnauthorized=()=>{cleared=true}
    await assert.rejects(api.request('/auth/password',{current_password:'engineering',new_password:'engineering-new'}),/当前密码不正确/)
    assert.equal(cleared,false)
  } finally {globalThis.fetch=previous}
})
