<script setup lang="ts">
import type { useAccount } from './auth'
const props = defineProps<{account: ReturnType<typeof useAccount>}>()
const {username, password, repeatPassword, authMode, busy, error, login} = props.account
function switchMode() { authMode.value = authMode.value === 'login' ? 'register' : 'login'; password.value = ''; repeatPassword.value = ''; error.value = '' }
</script>
<template>
  <section class="auth-panel">
    <span class="auth-eyebrow">MEMORIA · YOUR PRIVATE SPACE</span>
    <h2>{{ authMode==='login'?'欢迎回来':'创建你的账号' }}</h2>
    <p>登录后继续对话，Chat 与 Studio 共用账号。</p>
    <form @submit.prevent="login">
      <label>用户名<input v-model="username" name="username" autocomplete="username" required minlength="3" maxlength="32" placeholder="你的用户名" :disabled="busy" /></label>
      <label>密码<input v-model="password" name="password" type="password" :autocomplete="authMode==='login'?'current-password':'new-password'" required minlength="12" maxlength="128" placeholder="12–128 位密码" :disabled="busy" /></label>
      <label v-if="authMode==='register'">再次输入密码<input v-model="repeatPassword" name="repeat-password" type="password" autocomplete="new-password" required minlength="12" maxlength="128" :disabled="busy" /></label>
      <button type="submit" :disabled="busy">{{ busy?'正在连接…':authMode==='login'?'登录':'注册并登录' }}</button>
    </form>
    <button class="auth-mode" :disabled="busy" @click="switchMode">{{ authMode==='login'?'还没有账号？注册':'已有账号，返回登录' }}</button>
    <small>用户名：3–32 位小写字母、数字或下划线。资料按账号独立保存。</small>
  </section>
</template>
<style scoped>
.auth-panel{width:min(400px,100%);margin:48px auto;padding:36px;background:white;border:1px solid #e4e8e5;border-radius:20px;box-sizing:border-box;color:#26352f}.auth-eyebrow{font-size:10px;letter-spacing:2px;color:#7b8a83}.auth-panel h2{font-size:26px;letter-spacing:-1px;margin:18px 0 8px}.auth-panel p,.auth-panel small{font-size:12px;line-height:1.8;color:#79857e}.auth-panel label{display:grid;gap:8px;font-size:12px;margin:18px 0}.auth-panel input{width:100%;border:1px solid #dce3de;border-radius:9px;padding:12px;box-sizing:border-box;font:inherit;background:#fafcfb}.auth-panel input:focus{outline:2px solid #b3d3c3;border-color:#3c8061}.auth-panel button[type=submit]{width:100%;padding:12px;border:0;border-radius:9px;background:#246b50;color:white;font:inherit;cursor:pointer}.auth-mode{display:block;margin:20px auto;border:0;background:none;color:#397458;font-size:12px;cursor:pointer}.auth-panel small{display:block}.auth-panel button:disabled{opacity:.5;cursor:wait}
</style>
