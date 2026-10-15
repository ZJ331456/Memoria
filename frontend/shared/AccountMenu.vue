<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import type { useAccount } from './auth'
import PasswordPanel from './PasswordPanel.vue'
import FeishuPanel from './FeishuPanel.vue'

const props = defineProps<{account:ReturnType<typeof useAccount>; placement?:'up'|'down'; disabled?:boolean; manageAccounts?:boolean}>()
const emit = defineEmits<{manage:[]}>()
const {user,busy,api}=props.account
const root=ref<HTMLElement|null>(null),details=ref<HTMLDetailsElement|null>(null),trigger=ref<HTMLElement|null>(null)
const password=ref<InstanceType<typeof PasswordPanel>|null>(null),feishu=ref<InstanceType<typeof FeishuPanel>|null>(null)
function close(){if(details.value)details.value.open=false}
function outside(event:Event){if(!root.value?.contains(event.target as Node))close()}
function escape(event:KeyboardEvent){if(event.key==='Escape'&&details.value?.open){close();trigger.value?.focus()}}
async function openPassword(){close();await nextTick();password.value?.open()}
async function openFeishu(){close();await nextTick();feishu.value?.open()}
function manage(){close();emit('manage')}
onMounted(()=>{document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape)})
onUnmounted(()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape)})
</script>
<template>
  <div v-if="user" ref="root" class="account-control" :class="placement==='up'?'opens-up':'opens-down'">
    <details ref="details">
      <summary ref="trigger" :aria-label="user.username+' 的账号设置'"><span class="account-control-avatar">{{ user.username[0].toUpperCase() }}</span><span class="account-control-identity"><strong>{{ user.username }}</strong><small>{{ user.role==='superadmin'?'超级管理员 · 账号设置':'账号设置 · 退出 / 换号' }}</small></span><span class="account-control-arrow" aria-hidden="true">⌄</span></summary>
      <section class="account-popover" aria-label="账号操作"><header><span>当前登录账号</span><strong>{{ user.username }}</strong><small>对话、记忆和渠道绑定按账号独立保存</small></header><button :disabled="busy||disabled" @click="openPassword"><span>修改密码</span><small>验证旧密码后修改</small></button><button :disabled="busy||disabled" @click="props.account.switchAccount"><span>切换账号</span><small>退出后登录其他账号</small></button><button :disabled="busy||disabled" @click="openFeishu"><span>飞书连接</span><small>查看或管理本账号绑定</small></button><button v-if="manageAccounts&&user.role==='superadmin'" :disabled="busy||disabled" @click="manage"><span>账号管理</span><small>超级管理员操作</small></button><button class="account-signout" :disabled="busy||disabled" @click="props.account.logout">退出登录</button></section>
    </details>
    <PasswordPanel ref="password" :api="api" hide-trigger @changed="props.account.passwordChanged" @closed="trigger?.focus()"/>
    <FeishuPanel ref="feishu" :api="api" hide-trigger/>
  </div>
</template>
<style scoped>
.account-control{position:relative;min-width:165px;font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#3a5142}.account-control>details{margin:0}.account-control summary{list-style:none;display:flex;align-items:center;gap:10px;cursor:pointer;border:1px solid transparent;border-radius:10px;padding:8px;user-select:none}.account-control summary::-webkit-details-marker{display:none}.account-control summary:hover,.account-control details[open] summary{background:#e7eee2;border-color:#dce5d7}.account-control summary:focus-visible{outline:2px solid #5b9472;outline-offset:2px}.account-control-avatar{display:grid;place-items:center;width:32px;height:32px;border-radius:50%;background:#dfead7;color:#466947;flex-shrink:0;font-size:12px}.account-control-identity{min-width:0;flex:1}.account-control-identity strong{display:block;font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:150px}.account-control-identity small{font-size:9px;color:#7e8f77;display:block;margin-top:4px}.account-control-arrow{color:#7b9171;font-size:17px}.account-popover{position:absolute;right:0;width:254px;padding:7px;border:1px solid #dce5d7;border-radius:13px;background:white;box-shadow:0 14px 55px #1d3a272b;z-index:80;text-align:left}.opens-up .account-popover{bottom:calc(100% + 8px);left:0;right:auto}.opens-down .account-popover{top:calc(100% + 8px)}.account-popover header{display:grid;gap:6px;padding:12px;border-bottom:1px solid #edf1e9;margin-bottom:5px}.account-popover header>span{font-size:10px;color:#8c9b85}.account-popover header>strong{font-size:14px}.account-popover header>small{font-size:10px;color:#8c9b85;line-height:1.6}.account-popover>button{width:100%;display:block;text-align:left;padding:10px 12px;border:0;border-radius:8px;background:none;color:#38533f;cursor:pointer}.account-popover>button:hover{background:#f0f5eb}.account-popover>button>span{font-size:12px;display:block}.account-popover>button>small{display:block;font-size:9px;color:#8a9a81;margin-top:4px}.account-popover>.account-signout{border-top:1px solid #edf1e9;border-radius:0 0 8px 8px;margin-top:5px;color:#996b55;font-size:12px}.account-popover button:disabled{opacity:.45;cursor:wait}@media(max-width:650px){.account-control{min-width:150px}.account-control-identity strong{max-width:120px}.account-popover{width:min(254px,calc(100vw - 35px))}}
</style>
