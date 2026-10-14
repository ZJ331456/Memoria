export function dateLabel(value: string | null | undefined, detailed = false): string {
  if (!value) return '未记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未知时间'
  return new Intl.DateTimeFormat('zh-CN', {year:'numeric',month:'2-digit',day:'2-digit', ...(detailed ? {hour:'2-digit',minute:'2-digit'} as const : {})}).format(date)
}
export function textParts(text: string, query: string): {text:string; hit:boolean}[] {
  if (!query.trim()) return [{text,hit:false}]
  const escaped = query.trim().replace(/[.*+?^${}()|[\]\\]/g,'\\$&')
  const expression = new RegExp(escaped,'gi'), parts = []; let cursor=0
  for (const found of text.matchAll(expression)) {
    const index=found.index!
    if (index>cursor) parts.push({text:text.slice(cursor,index),hit:false})
    parts.push({text:found[0],hit:true}); cursor=index+found[0].length
  }
  if (cursor<text.length) parts.push({text:text.slice(cursor),hit:false})
  return parts
}
export function isCancelled(error: unknown): boolean {
  return (error as Error)?.name === 'AbortError' || (error as Error)?.constructor?.name === 'SessionChanged'
}
export function readableText(value:string):string {
  return value.replace(/(?<=[\u3400-\u9fff])[ \t]+(?=[\u3400-\u9fff，。！？、；：])|(?<=[，。！？、；：])[ \t]+(?=[\u3400-\u9fff])/g,'')
}
