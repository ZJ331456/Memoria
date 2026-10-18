<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import type { API } from '../../../shared/api'
import { isCancelled } from '../../../shared/console'
import MarkdownContent from '../../../shared/MarkdownContent.vue'
const props=defineProps<{api:API}>()
const rows=ref<any[]>([]),selected=ref<any>(null),hits=ref<any[]>([]),busy=ref(false),error=ref(''),notice=ref('')
const kind=ref('document'),title=ref(''),content=ref(''),authorship=ref('unknown'),query=ref(''),feedback=ref(''),outcome=ref('unknown')
const editing=ref(false),editTitle=ref(''),editContent=ref('')
const kinds:Record<string,string>={document:'文档',note:'笔记',task_result:'任务结果'},authors:Record<string,string>={self:'本人撰写',external:'第三方资料',unknown:'作者未知'}
let alive=true
onUnmounted(()=>{alive=false})
async function run(task:()=>Promise<void>){if(busy.value)return;busy.value=true;error.value='';notice.value='';try{await task()}catch(e){if(alive&&!isCancelled(e))error.value=(e as Error).message}finally{if(alive)busy.value=false}}
async function load(){const value=await props.api.request('/library/sources');if(alive)rows.value=value}
async function choose(id:string){const value=await props.api.request('/library/sources/'+encodeURIComponent(id));if(alive){selected.value=value;feedback.value='';outcome.value='unknown';editing.value=false}}
function edit(){if(!selected.value)return;editTitle.value=selected.value.revision.title;editContent.value=selected.value.revision.content;editing.value=true}
async function revise(){const item=selected.value;if(!item)return;await run(async()=>{await props.api.request('/library/sources/'+item.id+'/revisions',{expected_revision_id:item.revision.id,title:editTitle.value,content:editContent.value},'POST');if(alive){await choose(item.id);await load();hits.value=[];notice.value='修订已保存；旧版本保留，新版本需要重新提炼。'}})}
async function save(){await run(async()=>{const value=await props.api.request('/library/sources',{kind:kind.value,title:title.value,content:content.value,authorship:authorship.value},'POST');if(!alive)return;selected.value=value;title.value='';content.value='';notice.value='已保存原始资料，未创建聊天消息或个人Claim。';await load()})}
async function search(){await run(async()=>{const value=await props.api.request('/library/search',{query:query.value},'POST');if(alive)hits.value=value})}
async function recordFeedback(){const item=selected.value;if(!item)return;await run(async()=>{await props.api.request('/library/sources/'+item.id+'/feedback',{expected_revision_id:item.revision.id,outcome:outcome.value,content:feedback.value},'POST');if(alive)await choose(item.id)})}
async function distill(){const item=selected.value;if(!item)return;await run(async()=>{const value=await props.api.request('/library/sources/'+item.id+'/distill',{},'POST');if(alive){await choose(item.id);notice.value='本轮提炼：'+value.status}})}
async function retract(){const item=selected.value;if(!item||!window.confirm('撤回后退出检索，保留原始版本用于追溯。确认撤回？'))return;await run(async()=>{await props.api.request('/library/sources/'+item.id+'/retract',{},'POST');if(alive){selected.value=null;hits.value=[];await load()}})}
onMounted(()=>void run(load))
</script>
<template>
  <div class="library-page">
    <p class="library-intro">资料与个人事实分开：笔记、文档和任务结果直接进入当前账号的资料层；不自动变成“你喜欢什么”。任务经验结合明确反馈提炼，不自动发布可执行技能。</p>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <section class="panel"><h2>添加非对话资料</h2><form @submit.prevent="save">
      <div class="library-fields"><label>资料类型<select v-model="kind"><option value="document">文档</option><option value="note">笔记</option><option value="task_result">任务结果</option></select></label><label>作者归属<select v-model="authorship"><option value="unknown">未知</option><option value="self">本人撰写</option><option value="external">第三方资料</option></select></label></div>
      <label>标题<input v-model="title" maxlength="160" required placeholder="资料名称或任务名称"></label><label>正文<textarea v-model="content" rows="7" maxlength="120000" required placeholder="粘贴文本或 Markdown；任务结果请保留尝试、结果和可核对的验收信息。"></textarea></label>
      <button class="primary-button" :disabled="busy||!title.trim()||!content.trim()">保存资料</button><small>保存与检索不调用模型。点击提炼才会调用当前账号模型，最多两次；单次正文上限12000字符。支持文本/Markdown，暂不解析PDF、音频或图片。</small>
    </form></section>
    <section class="panel"><h2>检索资料与经验</h2><form class="library-search" @submit.prevent="search"><input v-model="query" maxlength="2000" required placeholder="输入资料主题"><button :disabled="busy||!query.trim()">检索</button></form>
      <article v-for="hit in hits" :key="hit.id" class="library-hit"><button class="text-button" @click="run(()=>choose(hit.source_id))">{{ hit.title }} →</button><p>{{ hit.citation }}</p><blockquote>{{ hit.quote }}</blockquote><p v-for="card in hit.knowledge_cards" :key="card.content">来源约束的观察：{{ card.content }}<br>适用范围：{{ card.applicability }}</p></article>
    </section>
    <div class="library-columns"><section class="panel"><h2>已保存资料（{{ rows.length }}）</h2><p v-if="!rows.length">暂无资料。可以直接添加笔记和文档，无需先发一条聊天消息。</p><button v-for="row in rows" :key="row.id" class="library-row" @click="run(()=>choose(row.id))"><strong>{{ row.title }}</strong><span>{{ kinds[row.kind] }} · {{ authors[row.authorship] }}</span></button></section>
      <section v-if="selected" class="panel"><h2>{{ selected.revision.title }}</h2><p>{{ kinds[selected.kind] }} · {{ authors[selected.authorship] }} · {{ selected.versions.length }} 个版本</p><details><summary>查看原始资料</summary><MarkdownContent :content="selected.revision.content" /></details>
        <button :disabled="busy" @click="edit">修订资料</button><form v-if="editing" @submit.prevent="revise"><label>修订标题<input v-model="editTitle" maxlength="160" required></label><label>修订正文<textarea v-model="editContent" rows="7" maxlength="120000" required></textarea></label><button :disabled="busy||!editTitle.trim()||!editContent.trim()">保存新版本</button></form>
        <template v-if="selected.kind==='task_result'"><h3>验收反馈</h3><p v-if="selected.feedback">最近报告：{{ selected.feedback.outcome }} · {{ selected.feedback.content }}</p><p>流程完成不代表任务成功；请写明验收标准、实际结果或失败现象。</p><label>结果<select v-model="outcome"><option value="unknown">尚不确定</option><option value="success">报告成功</option><option value="failure">报告失败</option></select></label><label>反馈<textarea v-model="feedback" rows="3" maxlength="3000"></textarea></label><button :disabled="busy||!feedback.trim()" @click="recordFeedback">保存反馈</button></template>
        <div class="library-actions"><button :disabled="busy" @click="distill">提炼知识 / 反思经验（调用模型）</button><button :disabled="busy" @click="retract">撤回资料</button></div>
        <article v-for="card in selected.cards" :key="card.id" class="library-hit"><strong>{{ card.available?'可用的来源观察':card.status==='approved'?'反馈已变化，旧经验停用':'需要辅助核对' }}</strong><p>{{ card.content }}</p><p>适用范围：{{ card.applicability }}</p><blockquote>{{ card.quote }}</blockquote><blockquote v-if="card.feedback_quote">反馈：{{ card.feedback_quote }}</blockquote></article>
      </section></div>
  </div>
</template>
<style scoped>
.library-page{display:grid;gap:20px}.library-intro{line-height:1.7;color:#607087}.library-fields,.library-search,.library-actions{display:flex;gap:12px;flex-wrap:wrap}.library-page form,.library-page label{display:grid;gap:8px}.library-page form{gap:14px}.library-page input,.library-page textarea,.library-page select{font:inherit;border:1px solid #dce3ed;border-radius:8px;padding:10px;width:100%;box-sizing:border-box}.library-page textarea{resize:vertical}.library-page small{display:block;color:#61718b;line-height:1.6}.library-columns{display:grid;grid-template-columns:minmax(220px,1fr) minmax(0,2fr);gap:20px}.library-row{display:grid;gap:8px;width:100%;padding:14px;text-align:left;border:0;border-bottom:1px solid #e8edf5;background:transparent;cursor:pointer}.library-row span{color:#6b7c94}.library-hit{margin-top:14px;padding:12px;border:1px solid #e5ebf5;border-radius:8px;overflow-wrap:anywhere}.library-hit blockquote{white-space:pre-wrap;border-left:3px solid #d2dcef;padding-left:12px;margin-left:0}.library-actions{margin-top:16px}.library-search{display:flex!important}.library-search input{flex:1;min-width:160px}@media(max-width:900px){.library-columns{grid-template-columns:1fr}}
</style>
