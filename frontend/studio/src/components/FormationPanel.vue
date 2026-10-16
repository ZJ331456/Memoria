<script setup lang="ts">
import { onMounted,onUnmounted,ref } from 'vue'
import type { API } from '../../../shared/api'
import { isCancelled,dateLabel } from '../../../shared/console'
const props=defineProps<{api:API}>(),emit=defineEmits<{source:[id:string]}>()
const status=ref<any>(null),observations=ref<any[]>([]),patterns=ref<any[]>([]),error=ref('')
let alive=true,timer:ReturnType<typeof setTimeout>
const kinds:Record<string,string>={fact:'事实',preference:'偏好',habit:'习惯',behavior:'行为',goal:'目标',workflow:'工作方式',experience:'经历',interest:'关注主题'}
const states:Record<string,string>={pending:'待审核',active:'有效推断',superseded:'已演化',retired:'已退出使用',noop:'未形成新结论',needs_assistance:'需辅助处理'}
async function load(){
  clearTimeout(timer)
  try{
    const [s,o,p]=await Promise.all([
      props.api.request('/memory-formation',undefined,'GET','formation-status'),
      props.api.request('/memory-observations?limit=30',undefined,'GET','formation-observations'),
      props.api.request('/memory-patterns?limit=20',undefined,'GET','formation-patterns')])
    if(alive){status.value=s;observations.value=o;patterns.value=p;error.value=''}
  }catch(e){if(alive&&!isCancelled(e))error.value=(e as Error).message}
  finally{if(alive)timer=setTimeout(()=>void load(),5000)}
}
onMounted(load);onUnmounted(()=>{alive=false;clearTimeout(timer)})
</script>
<template><section class="panel formation-panel"><div class="section-title"><div><h2>观察 → 模式 → 长期记忆</h2><p>单次行为先保留为观察，明确的长期自述可直接审核；跨会话证据足够后才归纳模式。</p></div><button class="text-button" @click="load">刷新</button></div><p v-if="error" role="alert">{{ error }}</p><p v-if="status&&!status.enabled">观察形成模块未启用，当前仍使用原来的直接候选提取。</p><p v-else-if="status">当前阈值：{{ status.config.min_sources }} 条不同原话、{{ status.config.min_sessions }} 个会话。重复复制不增加证据；模式是有边界的推断，长期效果尚未评测。</p><p v-for="failure in status?.consolidation_failures??[]" :key="failure.created_at" role="status">巩固异常记录：{{ dateLabel(failure.created_at,true) }} · {{ failure.reason }}。已保留观察，失败未记作空结果；后续新证据可再次触发整理。</p><details open><summary>近期观察（{{ observations.length }}）</summary><div class="table-scroll"><table v-if="observations.length" class="data-table"><thead><tr><th>时间 / 类别</th><th>观察与证据</th><th>形成路径</th><th>来源</th></tr></thead><tbody><tr v-for="item in observations" :key="item.id"><td>{{ dateLabel(item.observed_at,true) }}<br>{{ kinds[item.category]??item.category }}</td><td><strong>{{ item.topic }}</strong><p>{{ item.content }}</p><details><summary>查看引用原话</summary><blockquote>{{ item.quote }}</blockquote><p v-if="item.scope">适用范围：{{ item.scope }}</p></details></td><td>{{ item.asserted&&item.durable?'明确自述 · 另经审核':'行为观察 · 等待积累' }}</td><td><button class="text-button" @click="emit('source',item.message_id)">查看对话 →</button></td></tr></tbody></table><p v-else>尚无观察。升级前的历史不会自动批量重提取；新对话处理后在此出现。</p></div></details><details open><summary>跨会话模式与演化（{{ patterns.length }}）</summary><div class="table-scroll"><table v-if="patterns.length" class="data-table"><thead><tr><th>状态 / 操作</th><th>模式内容</th><th>证据与版本</th></tr></thead><tbody><tr v-for="item in patterns" :key="item.id"><td>{{ states[item.status]??item.status }}<br>{{ item.action }}</td><td><p>{{ item.content||'没有形成新的模式' }}</p><small>{{ item.reason }}</small></td><td>{{ item.evidence_message_ids.length }} 条原话<br>{{ item.previous_id?'有前序版本':'初次形成' }}<p v-for="id in item.evidence_message_ids" :key="id"><button class="text-button" @click="emit('source',id)">回查证据 →</button></p></td></tr></tbody></table><p v-else>尚无达到阈值并完成整理的模式。一条推荐请求不会自动成为永久爱好。</p></div></details></section></template>
<style scoped>.formation-panel details{margin-top:16px}.formation-panel summary{cursor:pointer;font-weight:600}.table-scroll{overflow:auto}.formation-panel blockquote{white-space:pre-wrap;margin:10px 0;padding:8px 12px;border-left:3px solid #ccd6e6}.formation-panel td{vertical-align:top;min-width:110px}.formation-panel td:nth-child(2){min-width:220px;max-width:620px;overflow-wrap:anywhere}</style>
