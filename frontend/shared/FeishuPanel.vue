<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import type { API } from './api'
import { isCancelled } from './console'
const props=defineProps<{api:API}>()
const open=ref(false),state=ref<any>(null),pair=ref<any>(null),error=ref(''),busy=ref(false),confirmUnbind=ref(false)
let alive=true,timer:ReturnType<typeof setTimeout>|undefined,version=0
async function refresh(){
  const current=version
  try{const result=await props.api.request('/channels/feishu',undefined,'GET','feishu-status');if(alive&&open.value&&current===version){state.value=result;error.value='';if(result.bound)pair.value=null}}
  catch(e){if(!isCancelled(e)&&alive&&current===version)error.value=(e as Error).message}
  finally{if(alive&&open.value&&current===version)timer=setTimeout(()=>void refresh(),3000)}
}
function show(){version++;open.value=true;state.value=null;error.value='';void refresh()}
function close(){version++;open.value=false;pair.value=null;confirmUnbind.value=false;clearTimeout(timer)}
async function issue(){const current=version;busy.value=true;error.value='';try{const result=await props.api.request('/channels/feishu/pairing',{});if(alive&&open.value&&current===version)pair.value=result}catch(e){if(alive&&current===version&&!isCancelled(e))error.value=(e as Error).message}finally{busy.value=false}}
async function unbind(){busy.value=true;try{await props.api.request('/channels/feishu/binding',undefined,'DELETE');confirmUnbind.value=false;pair.value=null;state.value={...state.value,bound:false}}catch(e){if(!isCancelled(e))error.value=(e as Error).message}finally{busy.value=false}}
onUnmounted(()=>{alive=false;close()})
</script>
<template>
  <button class="feishu-trigger" @click="show">连接飞书</button>
  <Teleport to="body"><div v-if="open" class="feishu-backdrop" @click.self="close" @keydown.esc="close"><section class="feishu-panel" role="dialog" aria-modal="true" aria-labelledby="feishu-title">
    <header><div><small>FEISHU · PRIVATE CONNECTION</small><h2 id="feishu-title">把飞书连接到你的账号</h2></div><button aria-label="关闭飞书连接" @click="close">×</button></header>
    <p>绑定后，私聊机器人会使用当前Memoria账号的记忆；聊天记录也保存到这个账号。群聊暂不接入。</p>
    <div v-if="error" class="feishu-error" role="alert">{{ error }}</div>
    <template v-if="state"><div class="feishu-state"><span>{{ !state.configured?'本机尚未配置机器人':state.connected?'机器人长连接在线':'长连接尚未就绪' }}</span><strong>{{ state.bound?'当前账号已绑定':'当前账号未绑定' }}</strong></div>
      <p v-if="state.last_error" class="feishu-error">状态码：{{ state.last_error }}。请按项目飞书机器人文档检查本机依赖、平台权限及发布状态。</p>
      <template v-if="state.configured&&!state.bound"><ol><li>点击下方按钮生成十分钟有效的一次性绑定码。</li><li>在手机飞书中私聊你的机器人，发送下方完整命令。</li><li>等待“绑定成功”，即可直接发送文字聊天。</li></ol><button class="feishu-primary" :disabled="busy||!state.connected" @click="issue">{{ pair?'重新生成绑定码':'生成绑定码' }}</button><div v-if="pair" class="feishu-code"><small>仅发送给你自己的机器人，不要公开此命令</small><code>{{ pair.command }}</code><span>有效至 {{ new Date(pair.expires_at*1000).toLocaleTimeString() }}；重新生成会使旧码失效。</span></div></template>
      <template v-if="state.bound"><p>可用命令：/new 新对话，/stop 停止生成，/unbind 解绑。手机和网页都能查看该账号的真实聊天记录。</p><button v-if="!confirmUnbind" class="feishu-danger" @click="confirmUnbind=true">解除当前账号绑定</button><div v-else class="feishu-confirm"><p>解绑会停止该渠道的在途生成，已有聊天记录仍保留在当前账号。确认解除？</p><button :disabled="busy" @click="unbind">确认解绑</button><button @click="confirmUnbind=false">取消</button></div></template>
    </template><p v-else>正在核对本机连接状态…</p>
  </section></div></Teleport>
</template>
<style scoped>
.feishu-trigger{border:1px solid #dfe5dd;border-radius:7px;background:white;color:#557452;padding:7px 11px;font-size:11px;cursor:pointer}.feishu-backdrop{position:fixed;inset:0;background:#18332266;z-index:1200;display:flex;align-items:center;justify-content:center;padding:20px}.feishu-panel{font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:white;color:#314335;border-radius:16px;padding:28px;width:min(530px,100%);max-height:90dvh;overflow:auto;box-shadow:0 24px 100px #102a2355}.feishu-panel header{display:flex;align-items:flex-start;justify-content:space-between;margin:0 0 20px}.feishu-panel header>button{border:0;background:none;font-size:22px;color:#8a9b88;cursor:pointer}.feishu-panel h2{font-size:19px;margin:12px 0 0}.feishu-panel small{color:#95a58f;font-size:9px;letter-spacing:1px}.feishu-panel p,.feishu-panel li{font-size:12px;line-height:1.9;color:#7d8c75}.feishu-panel ol{padding-left:18px}.feishu-state{display:flex;justify-content:space-between;gap:15px;background:#f3f7ef;border:1px solid #e0e9d9;border-radius:8px;padding:13px;font-size:11px;margin:20px 0}.feishu-state strong{font-weight:500}.feishu-error{background:#fff3e9;color:#976137!important;border-radius:7px;padding:10px;font-size:12px}.feishu-primary{background:#416d48;color:white;border:0;border-radius:8px;padding:11px 17px;cursor:pointer;font-size:12px}.feishu-panel button:disabled{opacity:.4;cursor:default}.feishu-code{margin:18px 0;padding:16px;border:1px dashed #bfceb4;border-radius:9px;display:grid;gap:12px}.feishu-code code{font-size:19px;letter-spacing:1.2px;color:#315e3d;user-select:all;overflow-wrap:anywhere}.feishu-code span{font-size:10px;color:#8d9c83}.feishu-danger{background:none;border:1px solid #e0d5cc;border-radius:7px;color:#9e6f51;padding:9px 13px;cursor:pointer;font-size:11px}.feishu-confirm button{margin:4px 10px 0 0;padding:8px 12px;border:1px solid #d3decb;border-radius:6px;background:white;cursor:pointer;font-size:12px}
</style>
