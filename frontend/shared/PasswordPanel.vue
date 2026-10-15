<script setup lang="ts">
import { nextTick, onUnmounted, ref } from 'vue'
import type { API } from './api'
import { isCancelled } from './console'

const props = defineProps<{api: API; disabled?: boolean}>()
const emit = defineEmits<{changed: []}>()
const dialog = ref<HTMLDialogElement | null>(null), currentInput = ref<HTMLInputElement | null>(null)
const current = ref(''), password = ref(''), repeat = ref(''), error = ref(''), busy = ref(false)
let alive = true
function clear() { current.value = ''; password.value = ''; repeat.value = ''; error.value = '' }
async function show() { clear(); dialog.value?.showModal(); await nextTick(); currentInput.value?.focus() }
function close() { if (!busy.value) { dialog.value?.close(); clear() } }
function cancel(event: Event) { if (busy.value) event.preventDefault() }
async function submit() {
  if (busy.value) return
  error.value = ''
  if (password.value !== repeat.value) { error.value = '两次输入的新密码不一致'; return }
  if (current.value === password.value) { error.value = '新密码不能与当前密码相同'; return }
  busy.value = true
  try {
    await props.api.request('/auth/password', {current_password: current.value, new_password: password.value})
    if (alive) { dialog.value?.close(); clear(); emit('changed') }
  } catch (e) {
    if (alive && !isCancelled(e)) error.value = (e as Error).message
  } finally { current.value = ''; busy.value = false }
}
onUnmounted(() => { alive = false; clear() })
</script>

<template>
  <button class="password-trigger" :disabled="disabled" @click="show">修改密码</button>
  <Teleport to="body"><dialog ref="dialog" class="password-dialog" aria-labelledby="password-title" aria-describedby="password-description" @cancel="cancel" @close="clear">
    <header><div><small>ACCOUNT SECURITY</small><h2 id="password-title">修改当前账号密码</h2></div><button type="button" aria-label="关闭修改密码" :disabled="busy" @click="close">×</button></header>
    <p id="password-description">修改后，当前账号的原有登录均会失效，需要使用新密码重新登录。对话与记忆仍保留在原账号下。</p>
    <p v-if="error" class="password-error" role="alert">{{ error }}</p>
    <form @submit.prevent="submit">
      <label>当前密码<input ref="currentInput" v-model="current" name="current-password" type="password" autocomplete="current-password" required minlength="12" maxlength="128" :disabled="busy" /></label>
      <label>新密码<input v-model="password" name="new-password" type="password" autocomplete="new-password" required minlength="12" maxlength="128" placeholder="12–128 个字符" :disabled="busy" /></label>
      <label>确认新密码<input v-model="repeat" name="confirm-new-password" type="password" autocomplete="new-password" required minlength="12" maxlength="128" :disabled="busy" /></label>
      <footer><button type="button" :disabled="busy" @click="close">取消</button><button class="password-primary" type="submit" :disabled="busy">{{ busy ? '正在修改…' : '修改并重新登录' }}</button></footer>
    </form>
  </dialog></Teleport>
</template>

<style scoped>
.password-trigger{font:inherit;font-size:11px;color:#557452;background:#fff;border:1px solid #dfe5dd;border-radius:7px;padding:8px 11px;cursor:pointer;white-space:nowrap}.password-dialog{width:min(440px,calc(100% - 32px));max-height:90dvh;overflow:auto;padding:28px;border:1px solid #dfe7dc;border-radius:18px;color:#304438;background:#fff;font-family:"Segoe UI","Microsoft YaHei",sans-serif;box-sizing:border-box;box-shadow:0 24px 90px #193f2830}.password-dialog::backdrop{background:#172d2866;backdrop-filter:blur(3px)}.password-dialog header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.password-dialog small{font-size:10px;letter-spacing:1.8px;color:#8a9a87}.password-dialog h2{font-size:20px;margin:10px 0 14px}.password-dialog header button{border:0;background:none;color:#7d8e7c;font-size:24px;cursor:pointer}.password-dialog p{font-size:12px;line-height:1.9;color:#758474;margin:0 0 18px}.password-dialog label{display:grid;gap:8px;margin:18px 0;color:#425846;font-size:12px}.password-dialog input{box-sizing:border-box;width:100%;font:inherit;font-size:14px;border:1px solid #dbe4d7;border-radius:9px;padding:12px;background:#fafcf8;color:#2b4032}.password-dialog input:focus{outline:2px solid #b7d4be;border-color:#5e8a69}.password-dialog footer{display:flex;justify-content:flex-end;gap:10px;margin-top:24px}.password-dialog footer button{font:inherit;font-size:12px;padding:10px 16px;border:1px solid #dbe4d7;border-radius:9px;background:#fff;color:#5b715d;cursor:pointer}.password-dialog .password-primary{background:#376b4a;border-color:#376b4a;color:white}.password-dialog .password-error{padding:10px;border-radius:8px;background:#fff1e9;color:#9b593d}.password-dialog button:disabled,.password-trigger:disabled{opacity:.5;cursor:wait}
</style>
