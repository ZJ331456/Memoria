# 展示与来源资产

[项目首页](../README.md) · [文档](../docs/README.md)

| 目录 | 内容 |
| --- | --- |
| [brand](brand/README.md) | 所有者提供的章鱼与字标素材，根README展示 |
| [images](images/project-diagrams.manifest.json) | 英文宣传海报、英文系统架构图；首页用PNG，架构另提供矢量SVG |
| [evaluation](evaluation/README.md) | 新双组评估状态与旧公开结果退役指纹 |
| [data-sources](data-sources/README.md) | 公开来源版本、许可、选择范围与SHA清单 |

图片和公开聚合统一存放在本目录。原话、参考答案、个人资料、密钥、模型权重和逐题调用账本保留在Git忽略的本机data/model目录。图表重绘不调用模型、不产生新评分。

```powershell
.\.venv\Scripts\python.exe scripts/render_project_diagrams.py
.\.venv\Scripts\python.exe scripts/render_project_diagrams.py --check
```

两张图采用统一的深蓝、淡紫与青色视觉。海报由内置ImageGen生成，英文文案与创意说明保存在[生成提示词](images/memoria-overview.prompt.txt)；选定PNG是原始资产，脚本不会重新生成或覆盖它。海报SVG只是嵌入该PNG的兼容容器，不是矢量海报。

架构图按当前[系统实现](../docs/implemented/系统架构与实现说明.md)绘制，区分写入审核、Claim条件访问、普通记忆混合检索、账号边界与可选核验，导出字体轮廓化矢量SVG。绘图依赖requirements.txt；本机字体为Segoe UI，其他环境可使用Inter或DejaVu Sans。已生成的PNG/SVG无需安装字体即可查看。

上述命令只重绘架构、封装现有海报并更新/核对指纹；不调用ImageGen或项目模型、不读取私有数据、不产生评分。SHA清单分别记录海报的1次创意图像生成、提示词和架构图来源。
