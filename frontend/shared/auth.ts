import { onMounted, onUnmounted, ref } from 'vue'
import { API, SessionChanged } from './api'

export function useAccount(hooks: {ready: () => Promise<void>; clear: () => void}) {
  const api = new API(), user = ref<any>(null), busy = ref(false), error = ref(''), notice = ref('')
  const username = ref(''), password = ref(''), repeatPassword = ref(''), authMode = ref('login')
  const events = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('memoria-auth') : null
  let version = 0
  function clear() {
    version++; api.reset(); user.value = null
    username.value = ''; password.value = ''; repeatPassword.value = ''; error.value = ''; notice.value = ''
    hooks.clear()
  }
  api.onUnauthorized = clear
  async function run(action: () => Promise<void>) {
    if (busy.value) return
    busy.value = true; error.value = ''
    try { await action() }
    catch (e) { if (!(e instanceof SessionChanged) && (e as Error).name !== 'AbortError') error.value = (e as Error).message }
    finally { busy.value = false }
  }
  async function restore() {
    const current = version
    try {
      const identity = await api.request('/auth/me', undefined, 'GET', 'auth-identity')
      if (current !== version) return
      user.value = identity; api.bindUser(identity.id); await hooks.ready()
    } catch (e) { if (!(e instanceof SessionChanged) && (e as Error).name !== 'AbortError') error.value = (e as Error).message }
  }
  async function login() {
    await run(async () => {
      api.reset(); version++; notice.value = ''
      try {
        if (authMode.value === 'register') {
          if (password.value !== repeatPassword.value) throw new Error('两次输入的密码不一致')
          await api.request('/auth/register', {username: username.value, password: password.value})
        }
        const result = await api.request('/auth/login', {username: username.value, password: password.value})
        user.value = result.user; api.bindUser(result.user.id); repeatPassword.value = ''
        events?.postMessage({type: 'identity-change'}); await hooks.ready()
      } finally { password.value = '' }
    })
  }
  async function logout() {
    await run(async () => {
      try { await api.request('/auth/logout', {}) }
      finally { clear(); events?.postMessage({type: 'identity-change'}) }
    })
  }
  async function switchAccount() {
    await logout()
    authMode.value = 'login'
    notice.value = error.value ? '本页资料已清空；服务端退出尚未确认，请先检查上方错误。' : '已退出当前账号，请登录你要切换到的账号。'
  }
  function passwordChanged() {
    clear()
    notice.value = '密码已修改，原有登录已失效。请使用新密码重新登录。'
    events?.postMessage({type: 'identity-change'})
  }
  async function check() {
    if (!user.value || document.hidden) return
    try {
      const identity = await api.request('/auth/me', undefined, 'GET', 'auth-check')
      if (identity.id !== user.value?.id) { clear(); await restore() }
      else user.value = identity
    } catch (e) { if (!(e instanceof SessionChanged) && (e as Error).name !== 'AbortError') error.value = (e as Error).message }
  }
  if (events) events.onmessage = event => { if (event.data?.type === 'identity-change') { clear(); void restore() } }
  onMounted(() => { window.addEventListener('focus', check); document.addEventListener('visibilitychange', check); void run(restore) })
  onUnmounted(() => { clear(); events?.close(); window.removeEventListener('focus', check); document.removeEventListener('visibilitychange', check) })
  return {api, user, busy, error, notice, username, password, repeatPassword, authMode, login, logout, switchAccount, passwordChanged, run}
}
