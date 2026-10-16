import { test } from 'node:test'
import assert from 'node:assert/strict'
import { channelLabel,processingLabel } from '../shared/memoryStatus.ts'
test('known channels, mixed sessions and missing legacy attribution are explicit',()=>{
  assert.equal(channelLabel({channels:['qq','chat']}),'QQ / 网页 Chat')
  assert.equal(channelLabel({channel:'telegram'}),'Telegram')
  assert.equal(channelLabel({kind:'imported'}),'导入资料')
  assert.equal(channelLabel({}),'渠道未记录')
})
test('zero candidates and need assistance never look like failed or disabled automation',()=>{
  assert.match(processingLabel({state:'no_candidates'}),/0条候选，未进入审核/)
  assert.match(processingLabel({state:'needs_assistance',approved:0,pending:1,rejected:0}),/需要人工辅助/)
  assert.match(processingLabel({state:'stored',approved:1}),/已审核入库/)
  assert.equal(processingLabel({state:'legacy_unknown'}),'旧任务结果未完整记录')
})
test('an observation waiting for evidence is not labelled as an approved preference',()=>{
  const label=processingLabel({state:'observing',observation_count:1})
  assert.match(label,/已记录 1 条观察/)
  assert.match(label,/尚未形成长期事实/)
  assert.doesNotMatch(label,/已审核入库/)
})
