import { test } from 'node:test'
import assert from 'node:assert/strict'
import { API, SessionChanged, sourceParts } from '../src/api.ts'

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
