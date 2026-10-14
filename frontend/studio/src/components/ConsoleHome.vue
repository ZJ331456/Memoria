<script setup lang="ts">
import { computed } from 'vue'
import { ElButton, ElTag } from 'element-plus'
import { dateLabel } from '../../../shared/console'
const props=defineProps<{summary:any;username:string}>()
defineEmits<{open:[page:string];conversation:[id:string]}>()
const stats=computed(()=>[
  {key:'sessions',label:'历史对话',unit:'段',hint:'当前账号保存的全部会话',icon:'▤',tone:'blue'},
  {key:'messages',label:'原始消息',unit:'条',hint:'可搜索、逐条定位的完整原话',icon:'≋',tone:'teal'},
  {key:'memories',label:'有效长期记忆',unit:'条',hint:'与原始对话分开管理',icon:'◈',tone:'purple'},
  {key:'pending_reviews',label:'待审核记忆',unit:'条',hint:'候选审核后才成为有效记忆',icon:'◎',tone:'amber'},
])
</script>
<template>
  <section class="welcome-strip"><div><span class="eyebrow">YOUR DATA, IN ONE PLACE</span><h2>{{ username }} 的数据工作空间</h2><p>先浏览真实对话，再核对来源、管理记忆与追踪变化。</p></div><el-button type="primary" @click="$emit('open','conversations')">浏览历史对话 →</el-button></section>
  <div class="stat-grid"><button v-for="s in stats" :key="s.key" class="stat-card" @click="$emit('open',s.key==='sessions'||s.key==='messages'?'conversations':s.key==='memories'?'memories':'reviews')"><span class="stat-icon" :class="s.tone">{{ s.icon }}</span><span class="stat-label">{{ s.label }}</span><div><strong>{{ summary?.[s.key]??'—' }}</strong><span>{{ s.unit }}</span></div><small>{{ s.hint }}</small></button></div>
  <div class="dashboard-grid"><section class="panel"><div class="section-title"><div><h2>最近的对话记录</h2><p>直接展开上下文，无需进入审核表单。</p></div><button class="text-button" @click="$emit('open','conversations')">查看全部 →</button></div><table v-if="summary?.recent?.length" class="data-table"><thead><tr><th>会话 / 原文预览</th><th>来源</th><th>消息</th><th>最近记录</th><th/></tr></thead><tbody><tr v-for="item in summary.recent" :key="item.id" @click="$emit('conversation',item.id)" tabindex="0" @keydown.enter="$emit('conversation',item.id)"><td><strong>{{ item.title }}</strong><p>{{ item.preview }}</p></td><td><el-tag size="small" :type="item.origin.kind==='imported'?'info':'success'">{{ item.origin.kind==='imported'?'导入资料':'本机对话' }}</el-tag></td><td>{{ item.message_count }}</td><td>{{ dateLabel(item.updated_at) }}</td><td class="table-arrow">↗</td></tr></tbody></table><div v-else class="empty"><span>▤</span><h3>当前账号尚无对话</h3><p>进入会话工作台创建自己的记录，其他账号的数据不会出现在这里。</p><a href="/app/" class="inline-link">打开会话工作台 →</a></div></section>
  <aside class="panel data-health"><div class="section-title"><h2>数据与记忆概况</h2><span class="live-indicator">实时读取</span></div><div class="health-row"><span>导入语料来源</span><strong>{{ summary?.datasets??'—' }}</strong></div><div class="health-row"><span>导入资料会话</span><strong>{{ summary?.imported_conversations??'—' }}</strong></div><div class="health-row"><span>结构化 Claim</span><strong>{{ summary?.claims??'—' }}</strong></div><div class="health-row"><span>待审 Claim</span><strong>{{ summary?.claim_statuses?.pending??0 }}</strong></div><div class="health-row"><span>待处理 / 重试任务</span><strong>{{ summary?.memory_jobs_pending??'—' }}</strong></div><div class="health-row"><span>失败任务</span><strong>{{ summary?.memory_jobs_failed??'—' }}</strong></div><div class="info-note"><strong>对话记录 ≠ 已批准记忆</strong><p>导入的公开对话保留原始来源；它们不会自动变成当前账号的个人事实。记忆为空时，仍可完整浏览历史。</p></div></aside></div>
  <div class="quick-links"><button @click="$emit('open','explorer')"><span>◈</span><div><strong>记忆审核与来源核对</strong><p>查看候选、条件和不可变修订</p></div><span>→</span></button><button @click="$emit('open','retrieval')"><span>⌕</span><div><strong>检索诊断</strong><p>检查候选、预算与证据覆盖</p></div><span>→</span></button><button @click="$emit('open','evaluation')"><span>▥</span><div><strong>实验账本</strong><p>查看本账号已有的真实运行记录</p></div><span>→</span></button></div>
</template>
