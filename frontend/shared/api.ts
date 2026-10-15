// Credentials stay in the server's HttpOnly cookie. No localStorage/sessionStorage.
export class SessionChanged extends Error {}
export class API {
  private readTimeoutMs: number
  constructor(readTimeoutMs = 15_000) { this.readTimeoutMs = readTimeoutMs }
  private epoch = 0
  private requests = new Set<AbortController>()
  private channels = new Map<string, AbortController>()
  private owner: string | null = null
  bindUser(id: string) { if (this.owner && this.owner !== id) this.reset(); this.owner = id }
  onUnauthorized: () => void = () => {}
  reset() { this.epoch++; this.requests.forEach(c => c.abort()); this.requests.clear(); this.channels.clear(); this.owner = null }
  async stream(path: string, body: unknown, onEvent: (event: any) => void): Promise<void> {
    const epoch=this.epoch, controller=new AbortController()
    this.requests.add(controller)
    const guard=()=>{if(epoch!==this.epoch)throw new SessionChanged()}
    try {
      const response=await fetch('/api'+path,{method:'POST',credentials:'same-origin',signal:controller.signal,
        headers:{'Content-Type':'application/json','Accept':'text/event-stream'},body:JSON.stringify(body)})
      guard()
      const owner=response.headers.get('X-Memoria-User')
      if(response.status===401||(this.owner&&owner&&owner!==this.owner)){this.onUnauthorized();throw new SessionChanged()}
      if(!response.ok)throw new Error('本轮对话未能开始（'+response.status+'），请稍后重试')
      await readEvents(response,guard,onEvent)
    } finally {this.requests.delete(controller)}
  }
  async request<T = any>(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST', channel?: string): Promise<T> {
    const epoch = this.epoch, controller = new AbortController()
    let timedOut = false
    const timer = setTimeout(() => { timedOut = true; controller.abort() }, method === 'GET' ? this.readTimeoutMs : 120_000)
    if (channel) { this.channels.get(channel)?.abort(); this.channels.set(channel, controller) }
    const changed = () => epoch !== this.epoch || (channel && this.channels.get(channel) !== controller)
    this.requests.add(controller)
    try {
      const response = await fetch('/api' + path, {method, credentials: 'same-origin', signal: controller.signal,
        headers: body === undefined ? {} : {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)})
      if (changed()) throw new SessionChanged()
      const owner=response.headers.get('X-Memoria-User')
      if (this.owner && owner && owner !== this.owner) { this.onUnauthorized(); throw new SessionChanged() }
      if (response.status === 401 && path !== '/auth/login') { this.onUnauthorized(); throw new SessionChanged() }
      const result = response.status === 204 ? undefined : await response.json()
      if (changed()) throw new SessionChanged()
      if (!response.ok) throw new Error(typeof result?.message === 'string' ? result.message : '请求失败，请刷新数据后重试（' + response.status + '）')
      return result as T
    } catch (e) {
      if (changed()) throw new SessionChanged()
      if (timedOut) throw new Error(method === 'GET'
        ? '读取超时，后台可能正在初始化；请稍后刷新。未返回的数据不代表历史为空。'
        : '请求等待超时，请先刷新核对结果，再决定是否重试。')
      throw e
    } finally { clearTimeout(timer); this.requests.delete(controller); if (channel && this.channels.get(channel) === controller) this.channels.delete(channel) }
  }
}

// Consume incrementally: UTF-8 characters and SSE frames may span network chunks.
export async function readEvents(response: Response, guard: () => void, onEvent: (event: any) => void): Promise<void> {
  if(!response.body)throw new Error('服务未返回对话流')
  const reader=response.body.getReader(),decoder=new TextDecoder()
  let pending=''
  function frame(value:string){
    if(value.length>2_000_000)throw new Error('对话流格式异常')
    const data=value.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n')
    if(data){guard();onEvent(JSON.parse(data))}
  }
  try {
    while(true){
      guard();const {value,done}=await reader.read();guard()
      pending+=done?decoder.decode():decoder.decode(value,{stream:true})
      let boundary:RegExpExecArray|null
      while((boundary=/\r?\n\r?\n/.exec(pending))){frame(pending.slice(0,boundary.index));pending=pending.slice(boundary.index+boundary[0].length)}
      if(pending.length>2_000_000)throw new Error('对话流格式异常')
      if(done){if(pending.trim())frame(pending);break}
    }
  } finally {await reader.cancel().catch(()=>{});reader.releaseLock()}
}
// Python spans use Unicode codepoints, JavaScript string.slice uses UTF-16 units.
export function sourceParts(text: string, start: number, end: number): string[] {
  const points = Array.from(text)
  if (start < 0 || end > points.length || start >= end) throw new Error('来源范围已失效')
  return [points.slice(0, start).join(''), points.slice(start, end).join(''), points.slice(end).join('')]
}
