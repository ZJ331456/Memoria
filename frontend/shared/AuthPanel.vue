<script setup lang="ts">
import { computed,ref,watch } from 'vue'
import type { useAccount } from './auth'
const props=defineProps<{account:ReturnType<typeof useAccount>}>()
const {username,password,repeatPassword,authMode,busy,error,notice,login}=props.account
const showPassword=ref(false),caps=ref(false)
const registration=computed(()=>authMode.value==='register')
function mode(value:string){authMode.value=value;password.value='';repeatPassword.value='';error.value='';showPassword.value=false;caps.value=false}
function keyboard(event:KeyboardEvent){caps.value=event.getModifierState('CapsLock')}
watch(authMode,()=>{showPassword.value=false;caps.value=false})
</script>
<template>
<section class="auth-panel">
  <div class="auth-brand-mark">m</div><span class="auth-eyebrow">YOUR PRIVATE MEMORY SPACE</span>
  <h2>{{ registration?'创建你的 Memoria 账号':'登录你的 Memoria' }}</h2>
  <p class="auth-description">{{ registration?'用一个独立账号保存对话、记忆与审核记录。':'继续自己的对话，管理有来源的长期记忆。' }}</p>
  <div class="auth-tabs" role="group" aria-label="登录或注册"><button type="button" :class="{selected:!registration}" :aria-pressed="!registration" :disabled="busy" @click="mode('login')">登录</button><button type="button" :class="{selected:registration}" :aria-pressed="registration" :disabled="busy" @click="mode('register')">注册账号</button></div>
  <p v-if="notice" role="status" class="auth-notice">{{ notice }}</p>
  <form @submit.prevent="login">
    <label>用户名<input v-model="username" name="username" autocomplete="username" required minlength="3" maxlength="32" pattern="[a-z][a-z0-9_]{2,31}" placeholder="输入用户名" :disabled="busy" @blur="username=username.trim().toLowerCase()"/><small v-if="registration">3–32位小写字母、数字或下划线，以字母开头。</small></label>
    <label>{{ registration?'设置密码':'密码' }}<span class="auth-secret"><input v-model="password" name="password" :type="showPassword?'text':'password'" :autocomplete="registration?'new-password':'current-password'" required minlength="12" maxlength="128" placeholder="12–128 个字符" :disabled="busy" @keydown="keyboard" @keyup="keyboard"/><button type="button" :aria-label="showPassword?'隐藏密码':'显示密码'" :disabled="busy" @click="showPassword=!showPassword">{{ showPassword?'隐藏':'显示' }}</button></span></label>
    <p v-if="caps" class="auth-caps" role="status">大写锁定已开启，请留意密码大小写。</p>
    <label v-if="registration">确认密码<input v-model="repeatPassword" name="repeat-password" :type="showPassword?'text':'password'" autocomplete="new-password" required minlength="12" maxlength="128" placeholder="再次输入相同密码" :disabled="busy"/></label>
    <small v-if="registration" class="auth-help">注册不会继承其他账号的历史或个人资料。</small>
    <button class="auth-submit" type="submit" :disabled="busy">{{ busy?'正在连接…':registration?'创建账号并登录':'登录并继续' }} <span v-if="!busy" aria-hidden="true">→</span></button>
  </form>
  <p class="auth-footnote">{{ registration?'已有账号？切换上方登录即可。':'忘记密码时请联系本机管理员重设。' }}</p>
  <div class="auth-privacy"><span>◇</span><p>Chat 与 Studio 共用账号。登录后，修改密码、切换账号和退出都在账号菜单中。</p></div>
</section>
</template>
<style scoped>
.auth-panel{width:min(420px,100%);margin:35px auto;padding:30px 32px;background:#fff;border:1px solid #e0e8dc;border-radius:20px;box-shadow:0 18px 65px #254b2a09;box-sizing:border-box;color:#2b4333;font-family:"Segoe UI","Microsoft YaHei",sans-serif}.auth-brand-mark{display:grid;place-items:center;width:35px;height:35px;border-radius:11px 11px 11px 3px;background:#36694a;color:white;font:30px/1 Georgia,serif;margin-bottom:17px}.auth-eyebrow{font-size:9px;letter-spacing:1.8px;color:#82957b}.auth-panel h2{font-size:23px;font-weight:600;margin:13px 0 8px;letter-spacing:-.6px}.auth-description{font-size:12px;color:#809078;line-height:1.8;margin:0 0 20px}.auth-tabs{display:flex;border:1px solid #e1e8dd;background:#f3f6ef;border-radius:9px;padding:3px;gap:3px;margin:20px 0}.auth-tabs button{width:50%;padding:9px;border:0;border-radius:6px;background:none;color:#8b9b82;font:inherit;font-size:12px;cursor:pointer}.auth-tabs .selected{background:white;color:#3f6d4b;box-shadow:0 1px 4px #315b3910}.auth-panel label{display:grid;gap:8px;font-size:12px;margin:18px 0;color:#4e674e}.auth-panel input{width:100%;box-sizing:border-box;border:1px solid #dce5d5;border-radius:8px;padding:12px;background:#fbfdf9;color:#304c37;font:inherit;font-size:13px}.auth-panel input:focus{outline:2px solid #b9d6bc;border-color:#5d9067}.auth-secret{position:relative;display:flex}.auth-secret input{padding-right:58px}.auth-secret button{position:absolute;right:9px;top:50%;transform:translateY(-50%);font-size:10px;border:0;background:none;color:#799372;cursor:pointer;padding:5px}.auth-panel small{font-size:10px;color:#8b9c83;line-height:1.7}.auth-help{display:block;margin:-4px 0 15px}.auth-submit{display:flex;align-items:center;justify-content:center;gap:15px;width:100%;background:#376c4b;color:white;border:0;border-radius:8px;padding:12px;font:inherit;font-size:13px;cursor:pointer;margin-top:20px}.auth-submit span{font-size:17px}.auth-footnote{font-size:10px;text-align:center;color:#8d9c84;margin:17px 0 22px}.auth-privacy{border-top:1px solid #edf1e8;padding-top:15px;display:flex;gap:10px;align-items:flex-start;color:#93a18b}.auth-privacy>span{font-size:17px}.auth-privacy p{font-size:10px;line-height:1.8;margin:0}.auth-panel .auth-notice{padding:10px 12px;background:#eef6eb;border:1px solid #d4e4cf;border-radius:8px;color:#3e7049;font-size:12px;line-height:1.7}.auth-caps{font-size:10px;color:#a17a44;margin:-10px 0 10px}.auth-panel button:disabled{opacity:.5;cursor:wait}@media(max-width:480px){.auth-panel{padding:25px 23px;margin:20px auto}.auth-panel h2{font-size:22px}}
</style>
