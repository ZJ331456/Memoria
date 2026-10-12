// Credentials stay in the server's HttpOnly cookie. No localStorage/sessionStorage.
export class SessionChanged extends Error {}
export class API {
  private epoch = 0
  private requests = new Set<AbortController>()
  onUnauthorized: () => void = () => {}
  reset() { this.epoch++; this.requests.forEach(c => c.abort()); this.requests.clear() }
  async request<T = any>(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST'): Promise<T> {
    const epoch = this.epoch, controller = new AbortController()
    this.requests.add(controller)
    try {
      const response = await fetch('/api' + path, {method, credentials: 'same-origin', signal: controller.signal,
        headers: body === undefined ? {} : {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)})
      if (epoch !== this.epoch) throw new SessionChanged()
      if (response.status === 401 && path !== '/auth/login') { this.onUnauthorized(); throw new SessionChanged() }
      const result = response.status === 204 ? undefined : await response.json()
      if (epoch !== this.epoch) throw new SessionChanged()
      if (!response.ok) throw new Error(typeof result?.message === 'string' ? result.message : '请求失败，请刷新数据后重试（' + response.status + '）')
      return result as T
    } finally { this.requests.delete(controller) }
  }
}
// Python spans use Unicode codepoints, JavaScript string.slice uses UTF-16 units.
export function sourceParts(text: string, start: number, end: number): string[] {
  const points = Array.from(text)
  if (start < 0 || end > points.length || start >= end) throw new Error('来源范围已失效')
  return [points.slice(0, start).join(''), points.slice(start, end).join(''), points.slice(end).join('')]
}
