<script setup lang="ts">
import { onMounted,ref } from 'vue'
import { ElButton,ElTag,ElAlert } from 'element-plus'
import { API } from '../../../shared/api'
import { dateLabel,isCancelled } from '../../../shared/console'
const props=defineProps<{api:API}>();const users=ref<any[]>([]),error=ref(''),loading=ref(false)
async function load(){loading.value=true;try{users.value=await props.api.request('/admin/users')}catch(e){if(!isCancelled(e))error.value=(e as Error).message}finally{loading.value=false}}
onMounted(()=>void load())
</script>
<template><section class="panel"><div class="section-title"><div><h2>账号管理</h2><p>管理账号元数据；个人会话与记忆仍由各自登录账号读取。</p></div><el-button :loading="loading" @click="load">刷新账号</el-button></div><el-alert v-if="error" :title="error" type="error"/><table class="data-table"><thead><tr><th>账号</th><th>角色</th><th>登录状态</th><th>创建记录</th></tr></thead><tbody><tr v-for="item in users" :key="item.id"><td><strong>{{ item.username }}</strong></td><td><el-tag :type="item.role==='superadmin'?'warning':'info'">{{ item.role==='superadmin'?'超级管理员':'普通用户' }}</el-tag></td><td><span class="status-indicator" :class="{off:!item.login_enabled}"/>{{ item.login_enabled?'已启用':'已停用' }}</td><td>{{ dateLabel(new Date(item.created_at*1000).toISOString(),true) }}</td></tr></tbody></table><div class="info-note">需要查看另一个用户的个人数据时，请退出后使用那个账号登录。此页面没有越权切换数据的入口。</div></section></template>
