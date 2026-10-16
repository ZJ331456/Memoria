<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import AuthPanel from '../../shared/AuthPanel.vue'
import AccountMenu from '../../shared/AccountMenu.vue'
import MarkdownContent from '../../shared/MarkdownContent.vue'
import { useAccount } from '../../shared/auth'
import { dateLabel, isCancelled } from '../../shared/console'
import MessageMemoryStatus from '../../shared/MessageMemoryStatus.vue'
import { channelLabel } from '../../shared/memoryStatus'

const conversations=ref<any[]>([]),total=ref(0),listPage=ref(1),search=ref(''),loading=ref(false)
const selected=ref(''),title=ref('新的对话'),messages=ref<any[]>([]),messageTotal=ref(0),oldestPage=ref(1),reading=ref(false)
const draft=ref(''),sending=ref(false),pendingUser=ref(''),answer=ref(''),phase=ref(''),mobileOpen=ref(false)
const feed=ref<HTMLElement|null>(null),composer=ref<HTMLTextAreaElement|null>(null)
const rawMessages=ref(new Set<string>()),copied=ref(''),followTail=ref(true)
const account=useAccount({ready:loadConversations,clear:clearData})
const {api,user,busy,error,logout}=account
let alive=true,timer:ReturnType<typeof setTimeout>,copyTimer:ReturnType<typeof setTimeout>,identity=0
let statusTimer:ReturnType<typeof setTimeout>,statusVersion=0
function stopStatuses(){statusVersion++;clearTimeout(statusTimer)}
async function refreshStatuses(){
  const id=selected.value,at=identity,version=statusVersion
  if(!id||!user.value||!messages.value.length)return
  try{
    const ids=messages.value.slice(-200).map(item=>item.id).join(',')
    const packet=await api.request('/studio/conversations/'+encodeURIComponent(id)+'/memory-status?'+new URLSearchParams({ids}),undefined,'GET','chat-memory-status')
    if(!alive||at!==identity||version!==statusVersion||selected.value!==id)return
    messages.value=messages.value.map(item=>({...item,...packet.items[item.id]}))
  }catch(e){if(alive&&at===identity&&version===statusVersion&&!isCancelled(e))error.value='记忆状态读取失败，可刷新消息重试'}
  finally{if(alive&&at===identity&&version===statusVersion&&selected.value===id)statusTimer=setTimeout(()=>void refreshStatuses(),5000)}
}
watch(selected,stopStatuses)
const selectedConversation=computed(()=>conversations.value.find(item=>item.id===selected.value))

function clearData(){stopStatuses();identity++;conversations.value=[];messages.value=[];messageTotal.value=0;total.value=0;selected.value='';title.value='新的对话';draft.value='';pendingUser.value='';answer.value='';phase.value='';sending.value=false;reading.value=false;loading.value=false;search.value='';listPage.value=1;oldestPage.value=1;mobileOpen.value=false;rawMessages.value.clear();copied.value='';followTail.value=true;clearTimeout(copyTimer)}
function fail(e:unknown){if(alive&&!isCancelled(e))error.value=(e as Error).message}
async function scrollBottom(force=false){if(!force&&!followTail.value)return;if(force)followTail.value=true;await nextTick();feed.value?.scrollTo({top:feed.value.scrollHeight,behavior:sending.value?'auto':'smooth'})}
function trackScroll(){const el=feed.value;if(el)followTail.value=el.scrollHeight-el.scrollTop-el.clientHeight<100}
function toggleRaw(id:string){rawMessages.value.has(id)?rawMessages.value.delete(id):rawMessages.value.add(id)}
async function copyMessage(message:any){const at=identity;try{await navigator.clipboard.writeText(message.content);if(alive&&at===identity){copied.value=message.id;clearTimeout(copyTimer);copyTimer=setTimeout(()=>copied.value='',1800)}}catch{if(alive&&at===identity)error.value='复制失败，请选择文字后复制'}}
async function loadConversations(){
  if(!user.value)return
  const q=search.value,p=listPage.value,at=identity;loading.value=true
  try{
    const result=await api.request('/studio/conversations?'+new URLSearchParams({origin:'native',q,page:String(p),page_size:'20'}),undefined,'GET','chat-list')
    if(!alive||at!==identity||q!==search.value||p!==listPage.value)return
    conversations.value=result.items;total.value=result.total;listPage.value=result.page
  }catch(e){fail(e)}finally{if(at===identity)loading.value=false}
}
function newChat(){if(sending.value)return;selected.value='';title.value='新的对话';messages.value=[];messageTotal.value=0;pendingUser.value='';answer.value='';reading.value=false;oldestPage.value=1;draft.value='';error.value='';mobileOpen.value=false;followTail.value=true;rawMessages.value.clear();void nextTick(()=>composer.value?.focus())}
async function choose(item:any){
  if(sending.value)return
  selected.value=item.id;title.value=item.title;messages.value=[];draft.value='';error.value='';mobileOpen.value=false
  await loadMessages(Math.max(1,Math.ceil(item.message_count/50)));await scrollBottom(true)
}
async function loadMessages(page:number,prepend=false){
  const id=selected.value,at=identity;if(!id)return
  reading.value=true
  try{
    const packet=await api.request('/studio/conversations/'+encodeURIComponent(id)+'/messages?'+new URLSearchParams({page:String(page),page_size:'50'}),undefined,'GET','chat-messages')
    if(!alive||at!==identity||selected.value!==id)return
    if(packet.session.origin.kind!=='native')throw new Error('导入资料请在Studio中查看')
    const height=feed.value?.scrollHeight??0
    messages.value=prepend?[...packet.items,...messages.value]:packet.items;messageTotal.value=packet.total;oldestPage.value=packet.page;title.value=packet.session.title
    stopStatuses();void refreshStatuses()
    if(prepend){await nextTick();if(feed.value)feed.value.scrollTop=feed.value.scrollHeight-height}
  }catch(e){fail(e)}finally{if(at===identity&&selected.value===id)reading.value=false}
}
async function send(){
  const text=draft.value.trim(),at=identity
  if(!text||sending.value||!user.value||busy.value||reading.value)return
  sending.value=true;error.value='';pendingUser.value=text;answer.value='';phase.value='正在思考';draft.value='';await scrollBottom(true)
  try{
    if(!selected.value){
      const session=await api.request('/sessions',{title:text.slice(0,36)})
      if(at!==identity)return
      selected.value=session.id;title.value=session.title
    }
    const id=selected.value;let terminal=false
    await api.stream('/sessions/'+encodeURIComponent(id)+'/chat/stream',{content:text},event=>{
      if(at!==identity||selected.value!==id)return
      if(event.type==='delta'){answer.value+=event.content??'';phase.value='正在回复';void scrollBottom()}
      else if(event.type==='tool')phase.value='正在使用工具'
      else if(event.type==='complete'){terminal=true;phase.value='已完成'}
      else if(event.type==='cancelled'){terminal=true;phase.value='已停止'}
      else if(event.type==='error')throw new Error('本轮生成失败，已保存记录可在Studio中核对')
    })
    if(at!==identity)return
    if(!terminal)throw new Error('连接中断，请核对已保存消息后再决定是否重试')
  }catch(e){if(at===identity)fail(e)}
  finally{
    if(alive&&at===identity){
      // Reload the durable record, including a user message saved before failure.
      pendingUser.value='';answer.value='';sending.value=false
      await loadConversations()
      if(selected.value){const count=selectedConversation.value?.message_count??messageTotal.value+2;await loadMessages(Math.max(1,Math.ceil(count/50)));await scrollBottom()}
      else draft.value=text
    }
  }
}
async function cancel(){try{await api.request('/sessions/'+encodeURIComponent(selected.value)+'/cancel',{});phase.value='正在停止'}catch(e){fail(e)}}
function keydown(event:KeyboardEvent){if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();void send()}}
watch(search,()=>{clearTimeout(timer);timer=setTimeout(()=>{listPage.value=1;void loadConversations()},250)})
watch(draft,async()=>{await nextTick();const el=composer.value;if(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,180)+'px'}})
onUnmounted(()=>{alive=false;stopStatuses();clearTimeout(timer);clearTimeout(copyTimer)})
</script>

<template>
  <div class="chat-shell">
    <div v-if="mobileOpen" class="sidebar-scrim" @click="mobileOpen=false" />
    <aside class="chat-sidebar" :class="{open:mobileOpen}">
      <a class="chat-brand" href="/"><span class="memoria-symbol">m</span><span>memoria<small>A LITTLE MORE FAMILIAR</small></span></a>
      <button class="new-chat" :disabled="!user||sending" @click="newChat"><span>＋</span> 开始新对话 <kbd>↗</kbd></button>
      <template v-if="user">
        <label class="chat-search"><span>⌕</span><input v-model="search" maxlength="200" aria-label="搜索我的对话" placeholder="搜索我的对话" :disabled="sending" /></label>
        <div class="conversation-label">我的对话 <span>{{ loading?'…':total }}</span><button aria-label="刷新对话列表" :disabled="loading||sending" @click="loadConversations">↻</button></div>
        <p class="chat-empty-list">此处显示日常聊天。<a href="/studio/#conversations">导入资料请到 Studio 的历史对话查看 →</a></p>
        <nav class="chat-list" aria-label="我的聊天记录" :aria-busy="loading">
          <p v-if="loading" class="chat-empty-list" role="status">正在读取聊天记录…</p>
          <button v-for="item in conversations" :key="item.id" :class="{chosen:selected===item.id}" :disabled="sending" @click="choose(item)"><strong>{{ item.title }}</strong><small>{{ channelLabel(item.origin) }} · {{ dateLabel(item.updated_at) }}</small></button>
          <p v-if="!loading&&!conversations.length" class="chat-empty-list">{{ search?'没有匹配的对话':'还没有聊天记录，开始聊聊吧。' }}</p>
        </nav>
        <div v-if="total>20" class="chat-list-pager"><button :disabled="listPage===1||sending" @click="listPage--;loadConversations()">上一页</button><span>{{ listPage }} / {{ Math.ceil(total/20) }}</span><button :disabled="listPage*20>=total||sending" @click="listPage++;loadConversations()">下一页</button></div>
      </template>
      <div v-else class="chat-sidebar-intro">每一段对话，<br>都从认识你开始。</div>
      <footer class="chat-sidebar-footer"><a href="/studio/">◇ <span>Memory Studio<small>查看资料与管理记忆</small></span> ↗</a><AccountMenu v-if="user" :key="user.id" :account="account" placement="up" :disabled="sending"/><div v-else class="chat-login-note">登录后保存自己的聊天记录</div></footer>
    </aside>
    <main class="chat-main">
      <header class="chat-header"><div><button class="mobile-menu" aria-label="打开会话列表" @click="mobileOpen=!mobileOpen">☰</button><span>Memoria <span class="header-slash">/</span> {{ selected?'对话':'Chat' }}</span><span class="chat-status"><i/> {{ sending?phase:'在这里，慢慢聊' }}</span></div><div><a href="/studio/">Studio ↗</a></div></header>
      <div v-if="error" class="chat-error" role="alert">{{ error }}<button aria-label="关闭提示" @click="error=''">×</button></div>
      <div v-if="!user" class="chat-login"><div class="login-heading"><span class="welcome-symbol">✳</span><h1>让每次对话，都有延续。</h1><p>与 Memoria 聊聊今天，也聊聊更长远的事。</p></div><AuthPanel :account="account" /></div>
      <template v-else>
        <div ref="feed" class="chat-feed" :class="{welcome:!selected&&!pendingUser}" aria-label="对话消息" aria-live="polite" @scroll="trackScroll">
          <section v-if="!selected&&!pendingUser" class="chat-welcome"><span class="welcome-kicker">YOUR SPACE, YOUR PACE</span><span class="welcome-symbol">✳</span><h1>今天，想聊些什么？</h1><p>从此刻的一件小事开始。<br>你的对话会保存在自己的账号下。</p><div class="welcome-detail"><span>○ 一段新的对话</span><span>◇ 记忆由你管理</span></div></section>
          <div v-else class="message-column">
            <button v-if="oldestPage>1" class="load-earlier" :disabled="reading||sending" @click="loadMessages(oldestPage-1,true)">{{ reading?'正在加载…':'加载更早的消息' }}</button>
            <div class="chat-conversation-title"><h1>{{ title }}</h1><span>仅当前账号可见</span></div>
            <p v-if="reading&&!messages.length" class="chat-loading">正在读取对话…</p>
            <article v-for="message in messages" :key="message.id" class="chat-message" :class="message.role"><span class="chat-message-avatar" aria-hidden="true">{{ message.role==='assistant'?'✳':'你' }}</span><div class="chat-message-body"><div class="chat-message-meta"><strong>{{ message.role==='assistant'?'Memoria':'你' }}</strong><time>{{ dateLabel(message.created_at,true) }}</time></div><MarkdownContent v-if="message.role==='assistant'&&!rawMessages.has(message.id)" class="chat-message-text" :content="message.content"/><div v-else class="chat-message-text">{{ message.content }}</div><MessageMemoryStatus :message="message"/><div class="message-actions"><button :aria-label="message.role==='assistant'?'复制助手回复':'复制我的消息'" @click="copyMessage(message)">{{ copied===message.id?'已复制':'复制' }}</button><button v-if="message.role==='assistant'" @click="toggleRaw(message.id)">{{ rawMessages.has(message.id)?'阅读模式':'查看原文' }}</button></div></div></article>
            <article v-if="pendingUser" class="chat-message user"><span class="chat-message-avatar">你</span><div class="chat-message-body"><div class="chat-message-meta"><strong>你</strong></div><div class="chat-message-text">{{ pendingUser }}</div></div></article>
            <article v-if="sending" class="chat-message assistant"><span class="chat-message-avatar">✳</span><div class="chat-message-body"><div class="chat-message-meta"><strong>Memoria</strong><span>{{ phase }}</span></div><MarkdownContent v-if="answer" class="chat-message-text" :content="answer"/><div v-else class="thinking-dots" role="status" aria-label="正在等待回复"><i/><i/><i/></div></div></article>
          </div>
        </div>
        <div class="composer-area"><button v-if="selected&&!followTail" class="jump-latest" @click="scrollBottom(true)">↓ 回到最新消息</button><form class="chat-composer" @submit.prevent="send"><textarea ref="composer" v-model="draft" aria-label="消息内容" rows="2" maxlength="20000" placeholder="给 Memoria 发消息…" :disabled="sending||busy||reading" @keydown="keydown"/><div class="composer-controls"><span>Enter 发送 · Shift + Enter 换行</span><button v-if="sending" type="button" class="stop-generation" @click="cancel">■ 停止</button><button v-else class="send-message" type="submit" aria-label="发送消息" :disabled="!draft.trim()||busy||reading">↑</button></div></form><div class="composer-note">回复由当前配置的模型生成，请核对重要信息。<a href="/studio/#conversations">导入资料与记忆管理 ↗</a></div></div>
      </template>
    </main>
  </div>
</template>
