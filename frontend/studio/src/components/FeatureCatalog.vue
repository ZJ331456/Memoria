<script setup lang="ts">
defineEmits<{open:[page:string]}>()
const entries=[
{page:'conversations',name:'历史与来源',use:'查看原话、搜索上下文，明确哪些资料属于公开导入。',need:'有历史即可使用'},
{page:'memories',name:'长期记忆',use:'浏览有效记忆、纠正旧版本、核对来源和导出可读层。',need:'先提出候选并明确批准'},
{page:'reviews',name:'候选审核',use:'把模型提取或手动提出的候选与原话核对，避免错误长期保存。',need:'至少一条待审候选'},
{page:'explorer',name:'结构化记忆',use:'把事实组织为主体、属性、值和适用条件，处理冲突与修订。',need:'经来源核对的结构化候选或事实'},
{page:'temporal',name:'双时间与变化',use:'分别查询事实何时成立、系统当时知道什么。未知时间不会补造。',need:'有相应事实、历史修订和时间证据'},
{page:'retrieval',name:'检索实验室',use:'查看实际召回、过滤、证据选择和预算；排查为什么答不出来。',need:'有记忆或可用原话；Dense需Embedding'},
{page:'evaluation',name:'评测运行记录',use:'读取既有实验的模型配置、指纹、失败与费用，帮助复现实验。',need:'本账号已保存实验账本；这里不运行新评测'},
{page:'pipeline',name:'记忆处理与服务',use:'查看任务状态、空结果、候选数量、模型连接及索引库存。',need:'随时可用，读取不调用模型'},
]
</script>
<template><section class="panel feature-catalog"><div class="section-title"><div><h2>功能地图：这些模块分别解决什么？</h2><p>日常管理放在主导航，结构化状态、时间查询和实验诊断收在高级区域。</p></div></div><div class="feature-table"><table class="data-table"><thead><tr><th>模块</th><th>实际用途</th><th>什么时候有结果</th><th/></tr></thead><tbody><tr v-for="item in entries" :key="item.page"><td><strong>{{ item.name }}</strong></td><td>{{ item.use }}</td><td>{{ item.need }}</td><td><button class="text-button" @click="$emit('open',item.page)">打开 →</button></td></tr></tbody></table></div><details class="api-capabilities"><summary>还有哪些后端能力没有完整的专用界面？</summary><ul><li>完整来源支持组和父依赖的可视化、来源撤销预览与治理。</li><li>跨记忆关联图、经历归档/遗忘策略、视图缓存依赖的专用管理。</li><li>共享Agent空间与ACL、技能/工具治理和受控Drift的可视化配置。</li><li>生产冻结重放、预算微实验和研究标注runner主要通过本机命令操作。</li></ul><p>这些机制分布在后端内部、接口或脚本中；实现范围不同，可用不等于已经有产品界面，也不等于已证明长期效果。<a href="/docs" target="_blank" rel="noopener noreferrer">查看当前接口文档 ↗</a></p></details></section></template>
<style scoped>.feature-table{overflow:auto}.feature-table td{min-width:130px;line-height:1.85;font-size:11px}.feature-table td:nth-child(2){min-width:230px}.feature-table td:nth-child(3){min-width:180px;color:#8a9c81}.api-capabilities{margin-top:22px;border-top:1px solid #e4ecdf;padding-top:17px;font-size:12px;color:#6e8766}.api-capabilities summary{cursor:pointer;font-weight:500}.api-capabilities li{line-height:1.9;margin:7px 0}.api-capabilities p{line-height:1.8;color:#8b9e82}.api-capabilities a{text-decoration:underline;text-underline-offset:3px}</style>
