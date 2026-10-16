<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElButton, ElInput, ElSelect, ElOption, ElTag, ElPagination, ElAlert, ElDialog } from 'element-plus'
import { API } from '../../../shared/api'
import MarkdownContent from '../../../shared/MarkdownContent.vue'
import MemoryCandidateDialog from '../../../shared/MemoryCandidateDialog.vue'
import MessageMemoryStatus from '../../../shared/MessageMemoryStatus.vue'
import { channelLabel } from '../../../shared/memoryStatus'
import { dateLabel, textParts, isCancelled,readableText } from '../../../shared/console'
const props=defineProps<{api:API; initialSession?:string; initialAnchor?:string}>()
const emit=defineEmits<{source:[id:string]; changed:[]; review:[]}>()
const candidate=ref<InstanceType<typeof MemoryCandidateDialog>|null>(null)
const query=ref(''),origin=ref('all'),sort=ref('recent'),listPage=ref(1),listing=ref<any>(null)
const selected=ref(''),detail=ref<any>(null),messagePage=ref(1),insideQuery=ref(''),focused=ref('')
const loading=ref(false),reading=ref(false),error=ref(''),inspector=ref<any>(null),inspectBusy=ref(false)
const extraction=ref<any>(null),extracting=ref(false)
async function extractMemory(){if(!extraction.value)return;extracting.value=true;try{await props.api.request('/messages/'+encodeURIComponent(extraction.value.id)+'/memory-extraction',{});if(alive){extraction.value=null;emit('changed');emit('review')}}catch(e){failure(e)}finally{if(alive)extracting.value=false}}
const compact=ref(false),readable=ref(true),markdown=ref(true)
const reader=ref<HTMLElement|null>(null)
let statusTimer:ReturnType<typeof setTimeout>,statusVersion=0
function stopStatuses(){statusVersion++;clearTimeout(statusTimer)}
async function refreshStatuses(){
  const id=selected.value,page=messagePage.value,version=statusVersion
  if(!id||!detail.value?.items.length)return
  try{
    const ids=detail.value.items.map((item:any)=>item.id).join(',')
    const result=await props.api.request('/studio/conversations/'+encodeURIComponent(id)+'/memory-status?'+new URLSearchParams({ids}),undefined,'GET','history-memory-status')
    if(!alive||selected.value!==id||messagePage.value!==page||statusVersion!==version)return
    detail.value={...detail.value,items:detail.value.items.map((item:any)=>({...item,...result.items[item.id]}))}
  }catch(e){if(alive&&selected.value===id&&statusVersion===version)failure(e)}
  finally{if(alive&&selected.value===id&&messagePage.value===page&&statusVersion===version)statusTimer=setTimeout(()=>void refreshStatuses(),5000)}
}
let listTimer:ReturnType<typeof setTimeout>,messageTimer:ReturnType<typeof setTimeout>,alive=true,listVersion=0,inspectVersion=0
const listKey=()=>[query.value,origin.value,sort.value,listPage.value].join('|')
const matchIndex=computed(()=>Math.max(0,detail.value?.matches?.findIndex((m:any)=>m.id===focused.value)??0))
function failure(e:unknown) { if(alive && !isCancelled(e)) error.value=(e as Error).message }
async function loadList(auto=true) {
  const key=listKey(),version=++listVersion;loading.value=true;error.value=''
  try {
    const params=new URLSearchParams({q:query.value,origin:origin.value,sort:sort.value,page:String(listPage.value),page_size:'20'})
    const result=await props.api.request('/studio/conversations?'+params,undefined,'GET','conversation-list')
    if(!alive||key!==listKey())return
    listing.value=result;listPage.value=result.page
    if(!result.total){selected.value='';detail.value=null;inspector.value=null;focused.value='';inspectBusy.value=false;inspectVersion++}
    if(auto && !selected.value && result.items.length) {
      const initial=result.items.find((s:any)=>s.id===props.initialSession)??result.items[0]
      await choose(props.initialSession||initial.id,props.initialAnchor||(query.value ? initial.preview_message_id : ''))
    }
  } catch(e) {failure(e)} finally {if(alive&&version===listVersion)loading.value=false}
}
async function choose(id:string,anchor='') {
  stopStatuses();selected.value=id;detail.value=null;inspector.value=null;inspectBusy.value=false;inspectVersion++;focused.value=anchor;insideQuery.value=query.value
  messagePage.value=1;await loadMessages(anchor)
}
async function loadMessages(anchor='') {
  const id=selected.value,q=insideQuery.value;if(!id)return
  reading.value=true;error.value=''
  try {
    const params=new URLSearchParams({page:String(messagePage.value),page_size:'50',q})
    if(anchor)params.set('anchor_id',anchor)
    const result=await props.api.request('/studio/conversations/'+encodeURIComponent(id)+'/messages?'+params,undefined,'GET','conversation-messages')
    if(!alive||selected.value!==id||insideQuery.value!==q)return
    detail.value=result;messagePage.value=result.page
    stopStatuses();void refreshStatuses()
    await nextTick()
    if(anchor) reader.value?.querySelector('[data-message-id="'+CSS.escape(anchor)+'"]')?.scrollIntoView({block:'center',behavior:'smooth'})
    else reader.value?.scrollTo({top:0})
  } catch(e) {failure(e)} finally {if(alive&&selected.value===id&&insideQuery.value===q)reading.value=false}
}
async function inspect(message:any) {
  const id=selected.value,version=++inspectVersion;focused.value=message.id;inspector.value=null;inspectBusy.value=true
  try {
    const result=await props.api.request('/claim-source/'+encodeURIComponent(message.id),undefined,'GET','conversation-source')
    if(alive&&version===inspectVersion&&id===selected.value&&focused.value===message.id)inspector.value=result
  } catch(e){if(version===inspectVersion)failure(e)} finally {if(alive&&version===inspectVersion)inspectBusy.value=false}
}
async function jumpMatch(delta:number) {
  const matches=detail.value?.matches??[];if(!matches.length)return
  const item=matches[(matchIndex.value+delta+matches.length)%matches.length]
  focused.value=item.id;await loadMessages(item.id)
}
function changeFilters() {clearTimeout(listTimer);listTimer=setTimeout(()=>{listPage.value=1;void loadList(false)},300)}
watch([query,origin,sort],changeFilters)
watch(insideQuery,()=>{clearTimeout(messageTimer);messageTimer=setTimeout(()=>void loadMessages(),300)})
onMounted(()=>void loadList())
onUnmounted(()=>{alive=false;stopStatuses();clearTimeout(listTimer);clearTimeout(messageTimer)})
</script>

<template>
  <div class="history-browser">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="notice" />
    <div class="history-toolbar"><div><h2>历史对话 <span class="count-label">{{ listing?.total ?? '—' }} 段</span></h2><p>搜索标题和完整原话，选择一段即可按顺序阅读。</p></div><div class="toolbar-actions"><el-button :loading="loading" @click="loadList(false)">刷新列表</el-button></div></div>
    <div class="history-layout" :class="{'has-inspector':inspector||inspectBusy}">
      <section class="conversation-sidebar panel">
        <div class="conversation-filters"><el-input v-model="query" maxlength="200" clearable placeholder="搜索全部标题 / 对话正文" aria-label="搜索全部历史对话" /><div class="filter-pair"><el-select v-model="origin" aria-label="来源类型"><el-option value="all" label="全部来源"/><el-option value="imported" label="导入资料"/><el-option value="native" label="本人对话（全部渠道）"/><el-option v-for="item in [{key:'chat',label:'网页 Chat'},{key:'qq',label:'QQ'},{key:'telegram',label:'Telegram'},{key:'feishu',label:'飞书'},{key:'api',label:'HTTP API'},{key:'unknown',label:'渠道未记录'}]" :key="item.key" :value="item.key" :label="item.label"/></el-select><el-select v-model="sort" aria-label="对话排序"><el-option value="recent" label="最近更新"/><el-option value="oldest" label="最早记录"/><el-option value="messages" label="消息最多"/></el-select></div></div>
        <div class="conversation-list" :aria-busy="loading">
          <button v-for="s in listing?.items??[]" :key="s.id" class="conversation-item" :class="{selected:s.id===selected}" @click="choose(s.id,query?s.preview_message_id:'')"><div class="conversation-item-top"><span class="origin-label">{{ channelLabel(s.origin) }}</span><time>{{ dateLabel(s.updated_at) }}</time></div><strong>{{ s.title }}</strong><p><template v-for="(part,index) in textParts(s.preview,query)" :key="index"><mark v-if="part.hit">{{ part.text }}</mark><span v-else>{{ part.text }}</span></template></p><div class="conversation-item-meta"><span>{{ s.message_count }} 条消息</span><span v-if="query&&s.matching_messages" class="search-hit">{{ s.matching_messages }} 条命中</span><span v-else>查看原文 →</span></div></button>
          <div v-if="!loading&&!listing?.items.length" class="empty"><h3>{{ query?'没有匹配的对话':'还没有对话记录' }}</h3><p>{{ query?'尝试其他关键词；搜索包含消息全文。':'当前账号没有资料。可以前往Chat开始自己的对话。' }}</p></div>
        </div>
        <div v-if="listing?.total" class="list-pagination"><span>共 {{ listing.total }} 段</span><el-pagination small layout="prev, pager, next" :pager-count="5" :current-page="listing.page" :page-size="20" :total="listing.total" @current-change="(p:number)=>{listPage=p;loadList(false)}" /></div>
      </section>
      <section class="conversation-reader panel" :class="{compact}" :aria-busy="reading">
        <template v-if="detail">
          <div class="reader-heading"><div><span class="eyebrow">{{ detail.session.origin.kind==='imported'?'IMPORTED SOURCE':'CONVERSATION' }}</span><h2>{{ detail.session.title }}</h2><div class="reader-meta"><span>{{ detail.total }} 条原话</span><span>记录于 {{ dateLabel(detail.session.created_at,true) }}</span><span>{{ channelLabel(detail.session.origin) }}</span><el-tag v-if="detail.session.origin.kind==='imported'" size="small" type="info">导入语料 · 事件时间未知</el-tag></div></div><div class="reader-view-actions"><el-button size="small" @click="markdown=!markdown">{{ markdown?'文本模式':'Markdown阅读' }}</el-button><el-button size="small" title="只调整逐字空格的中文显示，来源原文和指纹不变" @click="readable=!readable">{{ readable?'原文排版':'便于阅读' }}</el-button><el-button size="small" @click="compact=!compact">{{ compact?'舒适阅读':'紧凑排列' }}</el-button></div></div>
          <div class="within-search"><el-input v-model="insideQuery" maxlength="200" clearable placeholder="在本段对话内查找" aria-label="在当前对话中查找"/><span v-if="insideQuery" class="match-counter">{{ detail.matched_total }} 条命中</span><el-button v-if="insideQuery" size="small" :disabled="!detail.matches.length" @click="jumpMatch(-1)">上一处</el-button><el-button v-if="insideQuery" size="small" :disabled="!detail.matches.length" @click="jumpMatch(1)">下一处</el-button></div>
          <div ref="reader" class="message-log" :class="{loading:reading}"><article v-for="m in detail.items" :key="m.id" class="history-message" :class="{focused:focused===m.id,assistant:m.role==='assistant'}" :data-message-id="m.id"><div class="message-rail"><span class="message-avatar">{{ m.role==='assistant'?'M':'U' }}</span><span class="message-line"/></div><div class="message-main"><div class="message-header"><strong>{{ m.role==='assistant'?'助手答复':'用户原话' }}</strong><span>#{{ m.ordinal }}</span><time>{{ dateLabel(m.created_at,true) }}</time><button v-if="m.role==='user'&&detail.session.origin.kind==='native'" class="text-button" @click="extraction=m">LLM提取并处理</button><button v-if="m.role==='user'&&detail.session.origin.kind==='native'" class="text-button" @click="candidate?.open(m.id)">手动提出候选</button><button class="text-button" @click="inspect(m)">查看来源 ↗</button></div><MessageMemoryStatus :message="m"/><div class="message-content"><MarkdownContent v-if="markdown&&m.role==='assistant'&&!insideQuery.trim()" :content="readable?(m.display_content??m.content):m.content"/><template v-else v-for="(part,index) in textParts(readable?(m.display_content??m.content):m.content,readable?readableText(insideQuery):insideQuery)" :key="index"><mark v-if="part.hit">{{ part.text }}</mark><span v-else>{{ part.text }}</span></template></div></div></article><div v-if="!detail.items.length" class="empty"><h3>这段会话还没有消息</h3><p>历史记录会按原始顺序完整展示。</p></div></div>
          <div class="reader-pagination"><span>第 {{ detail.total?(detail.page-1)*50+1:0 }}–{{ Math.min(detail.page*50,detail.total) }} 条 / 共 {{ detail.total }} 条</span><el-pagination small layout="prev, pager, next, jumper" :current-page="detail.page" :page-size="50" :total="detail.total" @current-change="(p:number)=>{messagePage=p;loadMessages()}" /></div>
        </template><div v-else class="empty reader-empty"><span>{{ reading?'◌':'▤' }}</span><h3>{{ reading?'正在读取完整原话':'选择一段对话开始阅读' }}</h3><p>左侧可以搜索所有标题与消息，不需要逐个打开下拉框。</p></div>
      </section>
      <aside v-if="inspector||inspectBusy" class="source-inspector panel"><div class="section-title"><h2>来源详情</h2><button class="text-button" @click="inspector=null;inspectBusy=false;inspectVersion++">关闭 ✕</button></div><template v-if="inspector"><el-tag :type="inspector.personal_claim_eligible?'success':'info'">{{ inspector.personal_claim_eligible?'可提出待审记忆':'仅供浏览与诊断' }}</el-tag><p>{{ inspector.personal_claim_eligible?'原话可提取候选，低风险由LLM审核入库，其余留人工辅助。':'导入语料、非本人原话或撤销来源不自动形成个人事实。' }}</p><dl><dt>消息 ID</dt><dd>{{ inspector.message_id }}</dd><dt>角色</dt><dd>{{ inspector.role }}</dd><dt>记录时间</dt><dd>{{ dateLabel(inspector.created_at,true) }}</dd><dt>来源指纹 SHA256</dt><dd>{{ inspector.evidence?.content_sha256 }}</dd></dl><h3>完整原文</h3><div class="source-original">{{ inspector.content }}</div><el-button type="primary" plain @click="emit('source',inspector.message_id)">{{ inspector.personal_claim_eligible?'前往记忆候选审核':'前往来源核对' }}</el-button></template><p v-else>正在核对原文与来源指纹…</p></aside>
    </div>

  <el-dialog :model-value="!!extraction" title="从这条原话提取长期记忆" width="min(560px,94vw)" @close="extraction=null"><p>只处理当前账号这一条本人原话，调用提取与审核模型，可能产生费用。低风险且有证据支持的内容自动入库，其余保留辅助审核；临时提问可能不产生记忆。已有候选不重复抽取，旧空任务可按此操作重试并保留回执。</p><blockquote class="source-original">{{ extraction?.content }}</blockquote><template #footer><el-button @click="extraction=null">取消</el-button><el-button type="primary" :loading="extracting" @click="extractMemory">开始LLM提取与审核</el-button></template></el-dialog><MemoryCandidateDialog ref="candidate" :api="api" @submitted="emit('changed');emit('review')"/>
  </div>
</template>
