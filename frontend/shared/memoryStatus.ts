export const channelNames:Record<string,string>={chat:'网页 Chat',api:'HTTP API',qq:'QQ',telegram:'Telegram',feishu:'飞书',internal:'内部任务',unknown:'渠道未记录'}
export function channelLabel(origin:any):string {
  if(origin?.kind==='imported')return '导入资料'
  const channels=origin?.channels??[origin?.channel??'unknown']
  return channels.map((name:string)=>channelNames[name]??channelNames.unknown).join(' / ')
}
export function processingLabel(value:any):string {
  if(!value)return '状态尚未读取'
  const labels:Record<string,string>={not_applicable:'不作为本人记忆来源',not_queued:'未创建提取任务',pending:'等待自动提取',running:'正在自动提取',retry:'提取等待重试',failed:'提取失败',reviewing:'正在审核',awaiting_review:'候选待处理',needs_assistance:'需要人工辅助',no_candidates:'抽取完成 · 0条候选，未进入审核',stored:'已审核入库',rejected:'候选已拒绝',legacy_unknown:'旧任务结果未完整记录'}
  const label=labels[value.state]??'状态未知'
  return value.approved||value.pending||value.rejected ? `${label} · 入库 ${value.approved??0} / 待处理 ${value.pending??0} / 拒绝 ${value.rejected??0}` : label
}
