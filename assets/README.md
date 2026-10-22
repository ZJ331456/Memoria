# 展示与来源资产

[项目首页](../README.md) · [文档](../docs/README.md)

| 目录 | 内容 |
| --- | --- |
| [brand](brand/README.md) | 所有者提供的章鱼与字标素材，根README展示 |
| [images](images/project-diagrams.manifest.json) | 品牌记忆生命周期、当前系统架构；PNG与带轮廓文字/嵌入章鱼的SVG |
| [evaluation](evaluation/README.md) | 新双组评估状态与旧公开结果退役指纹 |
| [data-sources](data-sources/README.md) | 公开来源版本、许可、选择范围与SHA清单 |

图片和公开聚合统一存放在本目录。原话、参考答案、个人资料、密钥、模型权重和逐题调用账本保留在Git忽略的本机data/model目录。图表重绘不调用模型、不产生新评分。

```powershell
.\.venv\Scripts\python.exe scripts/render_project_diagrams.py
.\.venv\Scripts\python.exe scripts/render_project_diagrams.py --check
```

两张图采用所有者素材包的深蓝、蓝、紫与青色，嵌入原有章鱼，不额外生成装饰图片。旧宣传海报与其提示词已被当前架构/生命周期替换。脚本只离线绘图，核对字形边界并记录实现说明、渲染器、素材及四份输出SHA。SVG文字轮廓化，章鱼是嵌入位图。

旧LCCC/LoCoMo四方法的专用绘图脚本已移除；当前只发布双组新结果的脱敏聚合，不把历史F1迁入二分类栏。原始预测、评分、失败、账本与隔离SQLite只存被忽略的本机data。
