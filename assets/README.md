# 展示与来源资产

[项目首页](../README.md) · [文档](../docs/README.md)

| 目录 | 内容 |
| --- | --- |
| [images](images/project-diagrams.manifest.json) | 项目说明图、系统框架图，PNG用于首页，SVG用于缩放/导出 |
| [evaluation](evaluation/README.md) | 三组保留实验的聚合、图表与复现指纹 |
| [data-sources](data-sources/README.md) | 公开来源版本、许可、选择范围与SHA清单 |

图片和公开聚合统一存放在本目录。原话、参考答案、个人资料、密钥、模型权重和逐题调用账本保留在Git忽略的本机data/model目录。图表重绘不调用模型、不产生新评分。

```powershell
.\.venv\Scripts\python.exe scripts/render_project_diagrams.py
.\.venv\Scripts\python.exe scripts/render_project_diagrams.py --check
```

项目图按当前[系统实现](../docs/implemented/系统架构与实现说明.md)绘制，采用字体轮廓化SVG，避免依赖阅读端的中文字体。绘图依赖requirements-docs.txt；重绘时需要Microsoft YaHei或Noto Sans CJK等中文字体。已生成的PNG/SVG无需安装字体即可查看。
