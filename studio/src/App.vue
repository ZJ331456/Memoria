<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElAlert, ElButton, ElCheckbox, ElCollapse, ElCollapseItem, ElDialog, ElInput, ElInputNumber, ElOption, ElSelect, ElTag } from 'element-plus'
import { API, SessionChanged, sourceParts } from './api'
const api = new API(), user = ref<any>(null), busy = ref(false), error = ref('')
const username = ref(''), password = ref(''), page = ref('explorer')
const claims = ref<any[]>([]), slots = ref<any[]>([]), selected = ref<any>(null), history = ref<any[]>([])
const sessions = ref<any[]>([]), messages = ref<any[]>([]), source = ref<any>(null), sourceID = ref('')
const filter = ref(''), status = ref('all'), analysis = ref<any>(null), result = ref<any>(null)
const query = ref(''), mode = ref('unified'), selector = ref('coverage'), budget = ref(6000), topK = ref(8)
const predicate = ref(''), context = ref('{}'), fact = ref(''), recorded = ref(''), subject = ref('self')
const draftValue = ref(''), draftPredicate = ref('response_language'), draftScope = ref('{}'), draftPolarity = ref('positive')
const revisionAction = ref('correct'), factStart = ref(''), precision = ref('unknown')
const decision = ref(''), reason = ref(''), confirmOpen = ref(false), inferConditions = ref(false)
const runs = ref<any[]>([]), runDetail = ref<any>(null), unavailableRuns = ref(0)
const pretty = (v: any) => JSON.stringify(v, null, 2)
const labels: Record<string, string> = {residence:'居住地', occupation:'职业', school:'学校', response_language:'回答语言', response_detail:'回答详细度', diet:'饮食', work_habit:'工作习惯', hobby:'爱好', learning_goal:'学习目标', project_goal:'项目目标', time_preference:'时间偏好', budget:'预算'}
const visible = computed(() => claims.value.filter(c => (status.value === 'all' || c.status === status.value) && `${c.predicate} ${c.value ?? ''} ${c.id}`.includes(filter.value)))
function clear() {
  api.reset(); user.value = null; claims.value = []; slots.value = []; selected.value = null; history.value = []
  sessions.value = []; messages.value = []; source.value = null; sourceID.value = ''; analysis.value = null; result.value = null
  query.value = ''; context.value = '{}'; fact.value = ''; recorded.value = ''; subject.value = 'self';
  draftValue.value = ''; draftScope.value = '{}'; reason.value = ''; confirmOpen.value = false; password.value = ''
  runs.value = []; runDetail.value = null; unavailableRuns.value = 0
  factStart.value = ''; precision.value = 'unknown'; filter.value = ''; status.value = 'all'; username.value = ''
}
api.onUnauthorized = clear
async function run(fn: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await fn() } catch (e) { if (!(e instanceof SessionChanged) && (e as Error).name !== 'AbortError') error.value = (e as Error).message }
  finally { busy.value = false }
}
async function refresh() { claims.value = await api.request('/claims?limit=500'); slots.value = (await api.request('/claim-slots')).slots; sessions.value = await api.request('/sessions') }
async function login() {
  await run(async () => {
    try { const login = await api.request('/auth/login', {username: username.value, password: password.value}); user.value = login.user; await refresh() }
    finally { password.value = '' }
  })
}
async function logout() { await run(async () => { try { await api.request('/auth/logout', {}) } finally { clear() } }) }
async function choose(c: any) { await run(async () => { selected.value = await api.request('/claims/' + c.id); history.value = await api.request('/claims/' + c.id + '/history'); analysis.value = null }) }
async function loadSource(id = sourceID.value) { source.value = null; source.value = await api.request('/claim-source/' + encodeURIComponent(id)); sourceID.value = id }
function evidenceParts(e: any) { return source.value && source.value.message_id === e.message_id && source.value.evidence.content_sha256 === e.content_sha256 ? sourceParts(source.value.content, e.start, e.end) : null }
async function inspectEvidence(id: string) { await run(() => loadSource(id)) }
function propose(action: string) { decision.value = action; reason.value = ''; confirmOpen.value = true }
async function applyDecision() { await run(async () => {
  const c = selected.value
  if (!c) return
  await api.request('/claims/' + c.id + '/' + decision.value, {expected_revision: c.revision, reason: reason.value})
  selected.value = await api.request('/claims/' + c.id); history.value = await api.request('/claims/' + c.id + '/history')
  analysis.value = null; confirmOpen.value = false; await refresh()
}) }
async function createCandidate() { await run(async () => {
  if (!source.value?.personal_claim_eligible) throw new Error('此来源不能作为本人 Claim；公开语料只供诊断')
  selected.value = await api.request('/claims', {predicate: draftPredicate.value, value: draftValue.value, scope: JSON.parse(draftScope.value), subject_id: subject.value,
    polarity: draftPolarity.value, evidence: [source.value.evidence]})
  history.value = await api.request('/claims/' + selected.value.id + '/history'); await refresh()
}) }
async function extract() { await run(async () => {
  if (!source.value?.personal_claim_eligible) throw new Error('此来源不可用于个人抽取')
  await api.request('/claims/extract', {message_id: source.value.message_id, subject_id: subject.value, scope: JSON.parse(draftScope.value), infer_conditions: inferConditions.value})
  await refresh()
}) }
async function revise() { await run(async () => {
  if (!source.value?.personal_claim_eligible) throw new Error('请选择有效的新原话')
  const c = selected.value
  if (!c) return
  const updated = await api.request('/claims/' + c.id + '/revisions', {expected_revision: c.revision, action: revisionAction.value, value: draftValue.value,
    polarity: draftPolarity.value, modality: c.modality, evidence: [source.value.evidence], fact_start: factStart.value || null,
    fact_end: c.fact_end, time_precision: precision.value, memory_id: c.memory_id, reason: reason.value || '用户提交修订候选'})
  selected.value = updated; history.value = await api.request('/claims/' + updated.id + '/history'); analysis.value = null; await refresh()
}) }
async function assess(useModel = false) { await run(async () => {
  analysis.value = await api.request('/claims/' + selected.value.id + '/analyze', {expected_revision: selected.value.revision, use_model: useModel})
}) }
function queryBody() { return {query: query.value, predicates: predicate.value ? [predicate.value] : [], subject_id: subject.value,
    context: JSON.parse(context.value), fact_as_of: fact.value || null, recorded_as_of: recorded.value || null,
    mode: page.value === 'temporal' ? 'claim' : mode.value, selector: selector.value, budget_chars: budget.value, top_k: topK.value} }
async function access() { await run(async () => {
  const body = queryBody(), atPage = page.value
  const response = await api.request('/memory-query', body)
  if (atPage === page.value && pretty(body) === pretty(queryBody())) result.value = response
}) }
watch([query,mode,selector,budget,topK,predicate,context,fact,recorded,subject,page], () => { result.value = null })
watch(sourceID, () => { if (source.value?.message_id !== sourceID.value) source.value = null })
async function loadRuns() { await run(async () => { const records = await api.request('/research-runs'); runs.value = records.runs; unavailableRuns.value = records.unavailable }) }
async function inspectRun(id: string) { await run(async () => { runDetail.value = null; runDetail.value = await api.request('/research-runs/' + id) }) }
onMounted(() => run(async () => { user.value = await api.request('/auth/me'); await refresh() }))
</script>

<template>
  <div class="studio">
    <aside class="sidebar">
      <a class="brand" href="/studio/"><span class="brand-icon">m</span><span>memoria<small>MEMORY STUDIO</small></span></a>
      <div class="workspace-label">记忆工作空间</div>
      <nav aria-label="模块">
        <button v-for="(name, id) in {explorer:'记忆浏览与审核', temporal:'双时间与变化', retrieval:'检索实验室', evaluation:'评测运行记录'}" :key="id" :class="{active:page===id}" @click="page=id"><span>{{ id==='explorer'?'◈':id==='temporal'?'◷':id==='evaluation'?'▤':'⌕' }}</span>{{ name }}</button>
      </nav>
      <div class="sidebar-note"><span class="dot"></span> 来源优先，审核后生效<p>原话、候选与已批准状态分别展示。未知时间保持未知。</p></div>
      <a href="/docs" target="_blank" rel="noopener noreferrer" class="docs-link">API 文档 ↗</a>
    </aside>
    <main>
      <header><div><span class="eyebrow">MEMORIA / {{ page.toUpperCase() }}</span><h1>{{ page==='explorer'?'让每一条记忆都有来处':page==='temporal'?'理解变化，也保留当时所知':page==='evaluation'?'保留每次运行的证据':'看见一次访问如何发生' }}</h1></div>
        <div v-if="user" class="account"><el-tag>{{ user.username }}</el-tag><el-button :disabled="busy" @click="logout">退出登录</el-button></div>
      </header>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="notice" />
      <section v-if="!user" class="login card">
        <span class="eyebrow">你的私密记忆空间</span><h2>登录工作室</h2><p>访问本账号的来源、候选与版本。不同账号的数据独立保存。</p>
        <form @submit.prevent="login"><label>用户名<el-input v-model="username" autocomplete="username" /></label><label>密码<el-input v-model="password" type="password" autocomplete="current-password" /></label><el-button native-type="submit" type="primary" :loading="busy">登录</el-button></form>
        <small>首次账号可通过 API 注册；工作室不会保存明文密码或令牌。</small>
      </section>
      <template v-else>
        <div class="summary-row"><div><strong>{{ claims.length }}</strong><span>当前 Claim · 最多载入 500 条</span></div><div><strong>{{ claims.filter(c=>c.status==='pending').length }}</strong><span>待审核候选</span></div><div><strong>{{ sessions.length }}</strong><span>可见来源会话</span></div><p>公开语料属于实验空间，<br>不等于当前用户的个人事实。</p></div>
        <template v-if="page==='explorer'">
          <div class="explorer-grid">
            <section class="card list-panel"><div class="section-title"><h2>记忆索引</h2><el-button :disabled="busy" @click="run(refresh)">刷新</el-button></div>
              <el-input v-model="filter" placeholder="搜索值、槽位或 ID" clearable />
              <el-select v-model="status" aria-label="状态筛选"><el-option v-for="s in ['all','pending','approved','rejected','retracted','disputed']" :key="s" :label="s" :value="s" /></el-select>
              <button v-for="c in visible" :key="c.id" class="claim-row" :class="{chosen:selected?.id===c.id}" :disabled="busy" @click="choose(c)"><div><span>{{ labels[c.predicate] }}</span><el-tag size="small" :type="c.status==='approved'?'success':'info'">{{ c.status }}</el-tag></div><strong>{{ c.source_available ? c.value : '来源已失效' }}</strong><small>{{ c.subject_id }} · revision {{ c.revision }}</small></button>
              <div v-if="!visible.length" class="empty"><span>◈</span><h3>尚无符合条件的记忆</h3><p>只有带真实来源的候选才能进入审核。没有自动生成的演示画像。</p></div>
            </section>
            <section class="card details"><template v-if="selected"><div class="section-title"><h2>{{ labels[selected.predicate] }}</h2><el-tag>{{ selected.status }} · r{{ selected.revision }}</el-tag></div><h3 class="claim-value">{{ selected.value ?? '来源不可用' }}</h3>
                <dl><dt>主体</dt><dd>{{ selected.subject_id }}</dd><dt>条件</dt><dd>{{ pretty(selected.scope) }}</dd><dt>极性 / 模态</dt><dd>{{ selected.polarity }} / {{ selected.modality }}</dd><dt>事实区间</dt><dd>{{ selected.fact_start ?? '未知开始' }} → {{ selected.fact_end ?? '未限定结束' }}</dd><dt>系统获知</dt><dd>{{ selected.recorded_at }}</dd><dt>来源支持</dt><dd>原文范围核对；语义与现实支持未独立核验</dd></dl>
                <div v-for="e in selected.evidence" :key="e.message_id+':'+e.start" class="quote"><blockquote>{{ e.quote }}</blockquote><small>Unicode {{ e.start }}:{{ e.end }} · {{ e.message_id }}</small><el-button link type="primary" :disabled="busy" @click="inspectEvidence(e.message_id)">定位原话</el-button><p v-if="evidenceParts(e)">{{ evidenceParts(e)?.[0] }}<mark>{{ evidenceParts(e)?.[1] }}</mark>{{ evidenceParts(e)?.[2] }}</p></div>
                <div class="actions"><template v-if="selected.status==='pending'"><el-button type="primary" :disabled="busy" @click="propose('approve')">核对后批准</el-button><el-button :disabled="busy" @click="propose('reject')">拒绝</el-button></template><template v-else-if="selected.status==='approved'"><el-button :disabled="busy" @click="propose('dispute')">标记争议</el-button><el-button :disabled="busy" @click="propose('retract')">撤回</el-button></template><el-button v-else :disabled="busy" @click="propose('restore')">明确恢复</el-button><el-button :disabled="busy" @click="assess()">查看更新建议</el-button></div>
                <el-collapse><el-collapse-item title="可选模型支持度建议（可能调用远程服务，不会自动批准）"><el-button :disabled="busy" @click="assess(true)">显式调用模型评估</el-button></el-collapse-item></el-collapse><pre v-if="analysis">{{ pretty(analysis) }}</pre>
                <h3>不可变修订记录</h3><div v-for="(h, index) in history" :key="h.revision_id" class="revision"><span>r{{ h.revision }} / {{ h.status }}</span><strong>{{ h.value ?? '来源失效' }}</strong><p>{{ h.reason }}</p><small>获知 {{ h.recorded_at }} · 退休 {{ h.retired_at ?? '未退休' }}</small><p v-if="index>0 && history[index-1].value!==h.value" class="diff">值变化：{{ history[index-1].value ?? '不可用' }} → {{ h.value ?? '不可用' }}</p></div>
              </template><div v-else class="empty detail-empty"><span>↗</span><h3>选择一条记忆查看证据</h3><p>审核、条件与时间线会在这里展开。</p></div>
            </section>
          </div>
          <section class="card source-panel"><div class="section-title"><h2>从真实原话提出候选</h2><el-tag type="info">新候选始终 pending</el-tag></div>
            <div class="form-grid"><label>来源会话<el-select placeholder="选择本账号会话" @change="(id:string)=>run(async()=>{messages=await api.request('/sessions/'+id+'/messages?limit=1000')})"><el-option v-for="s in sessions" :key="s.id" :label="s.title" :value="s.id" /></el-select></label><label>来源消息<el-select placeholder="选择原话" @change="(id:string)=>run(()=>loadSource(id))"><el-option v-for="m in messages" :key="m.id" :label="m.role+' · '+m.content.slice(0,70)" :value="m.id" /></el-select></label><label>或消息 ID<el-input v-model="sourceID" /><el-button :disabled="busy || !sourceID" @click="run(()=>loadSource())">读取来源</el-button></label></div>
            <template v-if="source"><blockquote class="source-text">{{ source.content }}</blockquote><el-alert v-if="!source.personal_claim_eligible" title="此来源为公开语料、非本人用户原话或已撤销，仅可浏览；不能用于个人 Claim。" type="warning" :closable="false" /><div v-else class="form-grid"><label>槽位<el-select v-model="draftPredicate"><el-option v-for="s in slots" :key="s.predicate" :label="labels[s.predicate]" :value="s.predicate" /></el-select></label><label>候选值<el-input v-model="draftValue" maxlength="500" /></label><label>主体<el-input v-model="subject" /></label><label>条件 JSON<el-input v-model="draftScope" /></label><label>极性<el-select v-model="draftPolarity"><el-option label="positive" value="positive" /><el-option label="negative" value="negative" /></el-select></label></div>
              <div class="actions"><el-button type="primary" :disabled="busy || !source.personal_claim_eligible || !draftValue" @click="createCandidate">提交待审候选</el-button><el-checkbox v-model="inferConditions">限定面试条件提取</el-checkbox><el-button :disabled="busy || !source.personal_claim_eligible" @click="extract">显式模型抽取（可能收费）</el-button></div>
              <el-collapse v-if="selected?.status==='approved' && source.personal_claim_eligible"><el-collapse-item title="对所选 Claim 提交纠正 / 强化 / 变化候选"><div class="form-grid"><label>动作<el-select v-model="revisionAction"><el-option v-for="a in ['correct','reinforce','transition']" :key="a" :label="a" :value="a" /></el-select></label><label>事实开始（ISO 带时区）<el-input v-model="factStart" placeholder="未知时留空" /></label><label>精度<el-select v-model="precision"><el-option v-for="p in ['unknown','day','exact']" :key="p" :label="p" :value="p" /></el-select></label><label>原因<el-input v-model="reason" /></label></div><p>修订值 / 极性使用上方输入；主体和条件沿用原 Claim。强化必须保持原字段，变化必须提供可靠事实开始时间。</p><el-button :disabled="busy || !draftValue" @click="revise">提交修订，随后另行审核</el-button></el-collapse-item></el-collapse>
            </template>
          </section>
        </template>
        <template v-else-if="page==='evaluation'">
          <section class="card"><div class="section-title"><h2>本账号的真实实验账本</h2><el-button :loading="busy" @click="loadRuns">读取运行记录</el-button></div><p>仅展示执行记录和指纹校验后的 metrics。没有启动实验、自动发布、原始 prompt 或 gold 下载入口；字面评分、人工语义和长期效果分别验收。</p><el-alert v-if="unavailableRuns" :title="unavailableRuns+' 条记录不完整、归属不符或不可读取，未作为成功结果展示'" type="warning" :closable="false" />
            <div class="explorer-grid"><div><button v-for="r in runs" :key="r.run_id" class="claim-row" :disabled="busy" @click="inspectRun(r.run_id)"><div><span>{{ r.protocol.baseline ?? r.protocol.mode }}</span><el-tag size="small">{{ r.status }}</el-tag></div><strong>{{ r.run_id }}</strong><small>{{ r.code.commit?.slice(0,12) }} · {{ r.code.dirty?'dirty':'clean' }}</small></button><div v-if="!runs.length" class="empty"><h3>尚未载入可展示的运行记录</h3><p>点击读取，显示本账号已有记录；没有记录时保留空白。</p></div></div><div><template v-if="runDetail"><el-tag type="success">metrics SHA / 大小已核验</el-tag><h3>{{ runDetail.run_id }}</h3><pre>{{ pretty(runDetail) }}</pre></template><div v-else class="empty"><h3>选择一次运行</h3><p>查看数据 SHA、代码、角色参数、失败分母与执行耗时。自动诊断不是长期陪伴质量证明。</p></div></div></div>
          </section>
        </template>
        <template v-else>
          <section class="card lab"><div class="section-title"><h2>{{ page==='temporal'?'事实时间 × 系统获知时间':'真实候选 · 固定预算' }}</h2><el-tag>只读诊断 · 不调用 reader</el-tag></div><p>{{ page==='temporal'?'自然语言年份/月/日保留完整区间。对“当时知道什么”请明确系统获知截止时间。':'同一候选集并排比较 top-k 和覆盖选择；检索原话不会填补未经批准的个人状态。' }}</p>
            <label>查询<el-input v-model="query" placeholder="输入自己的记忆查询，不会写入会话" /></label><div class="form-grid"><label>槽位（空为规则解析）<el-select v-model="predicate" clearable><el-option v-for="s in slots" :key="s.predicate" :label="labels[s.predicate]" :value="s.predicate" /></el-select></label><label>主体<el-input v-model="subject" /></label><label>条件 JSON<el-input v-model="context" /></label><label>事实时间 · ISO 带时区<el-input v-model="fact" placeholder="未知 / 当前留空" /></label><label>获知截止 · ISO 带时区<el-input v-model="recorded" placeholder="默认当前系统所知" /></label><label v-if="page==='retrieval'">检索模式<el-select v-model="mode"><el-option v-for="m in ['unified','claim','keyword','bm25','dense','rrf']" :key="m" :label="m" :value="m" /></el-select></label><label>证据选择<el-select v-model="selector"><el-option label="coverage" value="coverage" /><el-option label="topk" value="topk" /></el-select></label><label>字符预算（非 token）<el-input-number v-model="budget" :min="256" :max="16000" /></label><label>最多证据组<el-input-number v-model="topK" :min="1" :max="32" /></label></div><el-button type="primary" :disabled="!query" :loading="busy" @click="access">执行访问</el-button>
          </section>
          <template v-if="result"><div class="result-bar"><el-tag :type="result.path==='clarify'?'warning':'success'">{{ result.path }}</el-tag><span>{{ result.reason }}</span><strong>{{ result.elapsed_ms }} ms</strong></div>
            <div class="comparison"><section v-for="s in ['topk','coverage']" :key="s" class="card"><h3>{{ s }}</h3><p>{{ result.comparison[s].payload.evidence.length }} 个完整证据组 · {{ result.comparison[s].cost_chars }} / {{ budget }} 字符</p><small>已覆盖 {{ result.comparison[s].covered.join(', ') || '无' }} · 缺少 {{ result.comparison[s].uncovered.join(', ') || '无' }}</small></section></div>
            <section class="card"><h2>选中的证据</h2><div v-for="e in result.selection.payload.evidence" :key="e.id" class="revision"><el-tag>{{ e.kind }}</el-tag><span>{{ e.id }}</span><pre>{{ pretty(e.data) }}</pre></div><div v-if="!result.selection.payload.evidence.length" class="empty"><h3>没有符合约束的证据</h3><p>请补充时间、条件或本人来源；系统没有用演示数据填充。</p></div></section>
            <section class="card"><el-collapse><el-collapse-item title="查询解析 / 候选分数 / 过滤原因 / 阶段耗时"><pre>{{ pretty(result) }}</pre></el-collapse-item></el-collapse></section>
          </template><section v-else class="card empty"><span>⌕</span><h3>从一次可解释的查询开始</h3><p>结果展示真实路径与缺项，尚未评测的质量收益不会显示为成绩。</p></section>
        </template>
      </template>
      <footer>Memoria Studio · 证据、状态与变化 · 数据来自当前登录账号</footer>
    </main>
    <el-dialog v-model="confirmOpen" title="明确执行审核决定" width="480px"><p>{{ decision }} · {{ selected?.predicate }} · revision {{ selected?.revision }}</p><p>请核对原话、主体、条件与时间；模型建议不代表事实已经验证。</p><label>原因<el-input v-model="reason" type="textarea" maxlength="500" /></label><template #footer><el-button @click="confirmOpen=false">取消</el-button><el-button type="primary" :disabled="!reason.trim()" :loading="busy" @click="applyDecision">确认 {{ decision }}</el-button></template></el-dialog>
  </div>
</template>
