<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import type { API } from '../../../shared/api'
import { isCancelled } from '../../../shared/console'
const props=defineProps<{api:API}>()
const rows=ref<any[]>([]),selected=ref<any>(null),hits=ref<any[]>([]),busy=ref(false),error=ref(''),notice=ref('')
const name=ref(''),repoPath=ref(''),description=ref('')
const since=ref(''),maxCommits=ref(200),includeBody=ref(true)
const title=ref(''),content=ref(''),kind=ref('document'),authorship=ref('unknown'),uploadFile=ref<File|null>(null)
const taskTitle=ref(''),taskDetail=ref(''),finishTask=ref<any>(null),outcome=ref('unknown'),feedback=ref(''),reportAuthorship=ref('unknown')
const query=ref('')
const kindLabels:Record<string,string>={document:'文档',note:'笔记',task_result:'任务结果'}
const authorLabels:Record<string,string>={self:'本人撰写',external:'第三方资料',unknown:'作者未知'}
const outcomeLabels:Record<string,string>={success:'报告成功',failure:'报告失败',unknown:'尚不确定'}
const eventLabels:Record<string,string>={project_created:'创建项目',source_bound:'绑定资料',task_opened:'新建任务',task_finished:'完成任务',project_archived:'归档项目'}
let alive=true
onUnmounted(()=>{alive=false})
async function run(task:()=>Promise<void>){if(busy.value)return;busy.value=true;error.value='';notice.value='';try{await task()}catch(e){if(alive&&!isCancelled(e))error.value=(e as Error).message}finally{if(alive)busy.value=false}}
async function load(){if(!alive)return;rows.value=await props.api.request('/projects');if(selected.value){const still=rows.value.find((row:any)=>row.id===selected.value.id);if(!still){selected.value=null;hits.value=[]}else selected.value=still}}
async function choose(id:string){if(!alive)return;selected.value=await props.api.request('/projects/'+encodeURIComponent(id));hits.value=[];finishTask.value=null}
async function create(){await run(async()=>{const value=await props.api.request('/projects',{name:name.value,repo_path:repoPath.value,description:description.value},'POST');if(!alive)return;name.value='';repoPath.value='';description.value='';await load();await choose(value.id);notice.value='项目已创建。先导入 Git 历史或资料，再开始任务循环。'})}
async function importGit(){const project=selected.value;if(!project)return;await run(async()=>{const value=await props.api.request('/projects/'+project.id+'/import/git',{since:since.value||null,max_commits:maxCommits.value,include_body:includeBody.value},'POST');if(!alive)return;await choose(project.id);notice.value=`已导入 ${value.commits} 个提交（来源 ${value.source.id.slice(0,8)}…），纯本地解析，未调用模型。`})}
function selectFile(event:Event){uploadFile.value=(event.target as HTMLInputElement).files?.[0]??null}
async function upload(){const project=selected.value,file=uploadFile.value;if(!project||!file)return;await run(async()=>{if(file.size>1048576)throw new Error('单文件上限1 MiB');const params=new URLSearchParams({filename:file.name,title:title.value,kind:kind.value,authorship:authorship.value});await props.api.request('/projects/'+project.id+'/import/file?'+params.toString(),file,'POST');if(!alive)return;uploadFile.value=null;await choose(project.id);notice.value='文件已解析入库并绑定到项目；原始字节与定位保留在资料层。'})}
async function saveText(){const project=selected.value;if(!project)return;await run(async()=>{await props.api.request('/projects/'+project.id+'/import/text',{title:title.value,content:content.value,kind:kind.value,authorship:authorship.value},'POST');if(!alive)return;title.value='';content.value='';await choose(project.id);notice.value='文本资料已保存并绑定到项目。'})}
async function openTask(){const project=selected.value;if(!project)return;await run(async()=>{await props.api.request('/projects/'+project.id+'/tasks',{title:taskTitle.value,detail:taskDetail.value},'POST');if(!alive)return;taskTitle.value='';taskDetail.value='';await choose(project.id);notice.value='任务已登记。开始前记得先检索项目记忆。'})}
async function report(){const project=selected.value,task=finishTask.value;if(!project||!task)return;await run(async()=>{await props.api.request('/projects/'+project.id+'/tasks/'+task.id+'/finish',{outcome:outcome.value,content:feedback.value,authorship:reportAuthorship.value},'POST');if(!alive)return;finishTask.value=null;feedback.value='';outcome.value='unknown';await choose(project.id);notice.value='结果与验收反馈已入库。经验提炼需在下方显式触发。'})}
async function distill(task:any){const project=selected.value;if(!project)return;await run(async()=>{const value=await props.api.request('/projects/'+project.id+'/tasks/'+task.id+'/distill',{},'POST');if(!alive)return;await choose(project.id);notice.value='本轮提炼状态：'+(value.status??JSON.stringify(value).slice(0,120))})}
async function search(){const project=selected.value;if(!project)return;await run(async()=>{const value=await props.api.request('/projects/'+project.id+'/recall',{query:query.value},'POST');if(!alive)return;hits.value=value.hits;notice.value=`命中 ${value.hits.length} / 检索 ${value.searched} 条；当前开放任务 ${value.open_tasks.length} 个。`})}
async function archive(){const project=selected.value;if(!project||!window.confirm('归档后项目退出列表，已有来源与任务保留在各自层内。确认归档？'))return;await run(async()=>{await props.api.request('/projects/'+encodeURIComponent(project.id),undefined,'DELETE');if(!alive)return;selected.value=null;await load()})}
</script>
<template>
  <div class="projects-page">
    <p class="projects-intro">项目记忆把一个仓库或一项工作的提交历史、设计文档和任务结果组织在一起：任务开始前先召回，结束后记录结果与反馈。导入与检索不调用模型；资料始终不自动变成个人 Claim。</p>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <section class="panel"><h2>新建项目工作区</h2>
      <form @submit.prevent="create">
        <div class="projects-fields"><label>项目名称<input v-model="name" maxlength="80" required placeholder="如：Memoria 后端"></label><label>仓库路径（可选，用于导入 Git 历史）<input v-model="repoPath" maxlength="500" placeholder="本机绝对路径，如 C:\works\Memoria"></label></div>
        <label>项目说明<input v-model="description" maxlength="2000" placeholder="一句话描述这个项目的目标"></label>
        <button class="primary-button" :disabled="busy||!name.trim()">创建项目</button>
      </form>
    </section>
    <div class="projects-columns">
      <section class="panel"><h2>项目（{{ rows.length }}）</h2>
        <p v-if="!rows.length">暂无项目。上面创建一个，或通过 Agent 工具 / MCP 使用。</p>
        <button v-for="row in rows" :key="row.id" class="projects-row" :class="{active:selected?.id===row.id}" @click="run(()=>choose(row.id))">
          <strong>{{ row.name }}</strong><span>{{ row.sources }} 份资料 · {{ row.open_tasks }} 个开放任务</span>
        </button>
      </section>
      <section v-if="selected" class="panel">
        <div class="projects-head"><h2>{{ selected.name }}</h2><button :disabled="busy" @click="archive">归档项目</button></div>
        <p v-if="selected.description">{{ selected.description }}</p><p v-if="selected.repo_path" class="projects-repo">仓库：{{ selected.repo_path }}</p>
        <details class="projects-events"><summary>最近动态（{{ selected.events.length }}）</summary><p v-for="event in selected.events" :key="event.id">{{ event.created_at.slice(0,19).replace('T',' ') }} · {{ eventLabels[event.action]??event.action }} {{ event.detail }}</p></details>

        <h3>导入 Git 提交历史</h3>
        <form @submit.prevent="importGit">
          <div class="projects-fields"><label>起始时间（git --since，可空）<input v-model="since" maxlength="64" placeholder="如 2026-06-01"></label><label>最多提交数<input v-model.number="maxCommits" type="number" min="1" max="2000"></label><label class="projects-check"><input v-model="includeBody" type="checkbox">包含提交说明正文</label></div>
          <button :disabled="busy||!selected.repo_path">导出并保存（不调用模型）</button><small v-if="!selected.repo_path">该项目未配置仓库路径；可在创建后通过 API 补充。</small>
        </form>

        <h3>添加文本资料 / 上传文件</h3>
        <form @submit.prevent="saveText">
          <div class="projects-fields"><label>资料类型<select v-model="kind"><option value="document">文档</option><option value="note">笔记</option></select></label><label>作者归属<select v-model="authorship"><option value="unknown">作者未知</option><option value="self">本人撰写</option><option value="external">第三方资料</option></select></label></div>
          <label>标题<input v-model="title" maxlength="160" required placeholder="架构决策、会议纪要、设计文档…"></label>
          <label>正文<textarea v-model="content" rows="5" maxlength="120000" required placeholder="粘贴文本或 Markdown；保留结论、条件与时间。"></textarea></label>
          <div class="projects-fields"><button class="primary-button" :disabled="busy||!title.trim()||!content.trim()">保存文本资料</button><label class="projects-file">文件（≤1 MiB，txt/md/csv/json/docx）<input type="file" accept=".txt,.text,.md,.markdown,.csv,.json,.jsonl,.docx" @change="selectFile"></label><button :disabled="busy||!uploadFile" @click.prevent="upload">解析并上传文件</button></div>
        </form>

        <h3>任务循环</h3>
        <form @submit.prevent="openTask">
          <div class="projects-fields"><label>任务标题<input v-model="taskTitle" maxlength="120" required placeholder="如：修复检索回归"></label><label>背景说明<input v-model="taskDetail" maxlength="4000" placeholder="目标、约束、验收标准"></label></div>
          <button :disabled="busy||!taskTitle.trim()">登记任务</button>
        </form>
        <article v-for="task in selected.tasks" :key="task.id" class="projects-task">
          <div class="projects-task-head"><strong>{{ task.title }}</strong><span>{{ task.status==='open'?'进行中':'已完成' }}</span></div>
          <p v-if="task.detail">{{ task.detail }}</p>
          <button v-if="task.status==='open'" :disabled="busy" @click="finishTask=task;feedback='';outcome='unknown'">记录结果</button>
          <form v-if="finishTask&&finishTask.id===task.id" class="projects-finish" @submit.prevent="report">
            <div class="projects-fields"><label>结果<select v-model="outcome"><option value="unknown">尚不确定</option><option value="success">报告成功</option><option value="failure">报告失败</option></select></label><label>结果归属<select v-model="reportAuthorship"><option value="unknown">作者未知</option><option value="self">本人确认</option><option value="external">外部报告</option></select></label></div>
            <label>结果与验收信息<textarea v-model="feedback" rows="3" maxlength="3000" required placeholder="写明验收标准与实际结果；失败同样有价值。"></textarea></label>
            <button :disabled="busy||!feedback.trim()">保存结果与反馈</button>
          </form>
          <p v-if="task.status==='done'">结果来源：{{ (task.source_id||'').slice(0,8) }}…<button class="text-button" :disabled="busy" @click="distill(task)">提炼经验（调用模型）</button></p>
        </article>

        <h3>项目内检索</h3>
        <form class="projects-search" @submit.prevent="search"><input v-model="query" maxlength="2000" required placeholder="为什么用这个方案 / 上次怎么修的 / 还有谁没完成"><button :disabled="busy||!query.trim()">检索</button></form>
        <article v-for="hit in hits" :key="hit.citation" class="projects-hit">
          <strong>{{ hit.title }}</strong><p>{{ hit.citation }}</p><blockquote>{{ hit.quote }}</blockquote>
          <p v-for="card in hit.knowledge_cards" :key="card.citation">来源约束的观察：{{ card.content }}<br>适用范围：{{ card.applicability }}<span v-if="card.reported_outcome"> · 当时结果：{{ outcomeLabels[card.reported_outcome]??card.reported_outcome }}</span></p>
        </article>

        <h3>已绑定资料（{{ selected.sources.length }}）</h3>
        <article v-for="source in selected.sources" :key="source.source_id" class="projects-hit">
          <strong>{{ source.title }}</strong><span> {{ kindLabels[source.kind]??source.kind }} · {{ authorLabels[source.authorship]??source.authorship }}<template v-if="source.outcome"> · {{ outcomeLabels[source.outcome]??source.outcome }}</template> · {{ source.live_status==='active'?'在库':source.live_status==='unavailable'?'已失效/撤回':source.live_status }}</span>
          <p v-if="source.origin">{{ source.origin }}</p>
        </article>
      </section>
    </div>
  </div>
</template>
<style scoped>
.projects-page{display:grid;gap:20px}.projects-intro{line-height:1.7;color:#607087}
.projects-page form,.projects-page label{display:grid;gap:8px}.projects-page form{gap:14px}
.projects-page input,.projects-page textarea,.projects-page select{font:inherit;border:1px solid #dce3ed;border-radius:8px;padding:10px;width:100%;box-sizing:border-box}
.projects-page textarea{resize:vertical}.projects-fields{display:flex;gap:12px;flex-wrap:wrap;align-items:end}
.projects-fields>label{flex:1;min-width:180px}.projects-check{flex:0!important;display:flex!important;align-items:center;gap:8px}.projects-check input{width:auto}
.projects-check{grid-template-columns:none}.projects-file input{padding:8px 0;border:0;background:transparent}
.projects-columns{display:grid;grid-template-columns:minmax(220px,1fr) minmax(0,2fr);gap:20px}
.projects-row{display:grid;gap:6px;width:100%;padding:14px;text-align:left;border:1px solid transparent;border-bottom:1px solid #e8edf5;background:transparent;cursor:pointer}
.projects-row span{color:#6b7c94}.projects-row.active,.projects-row:hover{border:1px solid #d2dcef;border-radius:10px;background:#f7faff}
.projects-head{display:flex;justify-content:space-between;align-items:center;gap:12px}
.projects-repo{font-size:13px;color:#61718b;overflow-wrap:anywhere}
.projects-events{margin:6px 0 12px}.projects-events p{color:#61718b;font-size:13px;line-height:1.7}
.projects-search{display:flex!important}.projects-search input{flex:1;min-width:160px}
.projects-task,.projects-hit{margin-top:12px;padding:12px;border:1px solid #e5ebf5;border-radius:8px;overflow-wrap:anywhere;display:grid;gap:8px}
.projects-task-head{display:flex;justify-content:space-between;gap:12px}.projects-task-head span{color:#6b7c94}
.projects-hit blockquote{white-space:pre-wrap;border-left:3px solid #d2dcef;padding-left:12px;margin:0}
.projects-hit span{color:#6b7c94;font-size:13px}.projects-hit p{margin:0;color:#3d4a5c}
.text-button{background:transparent;border:0;color:#2563eb;cursor:pointer;padding:0 6px}
button{font:inherit;border:1px solid #dce3ed;border-radius:8px;padding:8px 14px;background:#fff;cursor:pointer}
button:disabled{opacity:.5;cursor:not-allowed}.primary-button{background:#1f2937;color:#fff;border-color:#1f2937}
small{color:#61718b;line-height:1.6}
@media(max-width:900px){.projects-columns{grid-template-columns:1fr}}
</style>
