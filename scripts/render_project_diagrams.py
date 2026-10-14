"""Render source-backed project diagrams; no models, private data or evaluation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets/images"
NAMES = ("memoria-overview", "memoria-architecture")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render():
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".venv/.matplotlib-cache"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Circle

    candidates = ("Microsoft YaHei", "Noto Sans CJK SC", "PingFang SC", "SimHei")
    font = None
    for name in candidates:
        try:
            font = font_manager.findfont(name, fallback_to_default=False)
            break
        except ValueError:
            pass
    if font is None:
        raise RuntimeError("Install a Chinese font: Microsoft YaHei or Noto Sans CJK SC.")
    plt.rcParams.update({"svg.fonttype": "path", "svg.hashsalt": "memoria-docs-v1"})
    palette = {"ink": "#182C48", "muted": "#63758D", "line": "#DCE5F0",
               "paper": "#F5F8FC", "teal": "#087F8C", "blue": "#3769C8"}

    def canvas(height, eyebrow, title, subtitle):
        fig = plt.figure(figsize=(16, height / 100), facecolor=palette["paper"])
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 1600); ax.set_ylim(height, 0); ax.axis("off")
        text(ax, 72, 43, eyebrow, 11, palette["teal"], weight="bold")
        text(ax, 72, 98, title, 28, weight="bold")
        text(ax, 74, 148, subtitle, 14, palette["muted"])
        return fig, ax

    def text(ax, x, y, label, size=16, color=None, weight="normal", ha="left"):
        props = font_manager.FontProperties(fname=font, size=size, weight=weight)
        ax.text(x, y, label, fontproperties=props, color=color or palette["ink"],
                va="center", ha=ha, linespacing=1.7, zorder=5)

    def box(ax, x, y, w, h, face="white", edge=None, radius=18, shadow=False):
        if shadow:
            ax.add_patch(FancyBboxPatch((x, y + 6), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                                      facecolor="#E8EEF6", edgecolor="none", zorder=1))
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                                  facecolor=face, edgecolor=edge or palette["line"], lw=1, zorder=2))

    def arrow(ax, start, end, color="#8EA5BE", both=False):
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="<->" if both else "-|>",
                                    mutation_scale=14, lw=1.6, color=color, zorder=3))

    def save(fig, name):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        fig.savefig(OUTPUT / f"{name}.png", dpi=150, metadata={"Software": "Memoria diagram renderer"})
        path = OUTPUT / f"{name}.svg"
        fig.savefig(path, metadata={"Date": None, "Creator": "Memoria diagram renderer"})
        path.write_text("\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").splitlines()) + "\n",
                        encoding="utf-8", newline="\n")
        plt.close(fig)

    fig, ax = canvas(780, "MEMORIA  /  PROJECT OVERVIEW", "为长期陪伴建立可追溯的记忆",
                     "以原话为依据，区分候选与事实，让记忆带着来源、条件和版本被使用。")
    cards = [
        ("原话与来源", "会话、消息、资料", "保留原文与来源定位", "#087F8C", "#E8F6F5"),
        ("候选与审核", "先抽取候选，再明确审核", "批准后进入长期记忆", "#3769C8", "#EDF2FF"),
        ("条件与版本", "何时成立，何时发生变化", "区分当前状态与历史", "#795BB6", "#F2EDFA"),
        ("证据与回答", "检索、预算与引用复核", "在有限上下文中使用记忆", "#B56D2B", "#FFF3E7"),
    ]
    for i, (title, line1, line2, color, tint) in enumerate(cards):
        x = 75 + i * 370
        box(ax, x, 208, 340, 290, shadow=True)
        ax.add_patch(Circle((x + 48, 255), 24, facecolor=tint, edgecolor="none", zorder=4))
        text(ax, x + 48, 255, f"0{i + 1}", 13, color, "bold", "center")
        text(ax, x + 28, 321, title, 23, color, "bold")
        text(ax, x + 28, 382, line1, 15)
        text(ax, x + 28, 425, line2, 15, palette["muted"])
        if i < 3:
            arrow(ax, (x + 345, 353), (x + 363, 353))
    for x, heading, body, color in [
        (75, "Chat · 自然对话", "新会话、流式回复与停止生成", palette["teal"]),
        (570, "Studio · 管理记忆", "审核、来源回查与检索解释", palette["blue"]),
        (1065, "Research · 验证机制", "冻结来源、记录调用与评测", "#795BB6"),
    ]:
        box(ax, x, 552, 460, 125, face="#FFFFFF", radius=14)
        text(ax, x + 24, 590, heading, 18, color, "bold")
        text(ax, x + 24, 636, body, 13, palette["muted"])
    text(ax, 78, 731, "当前定位：本机研究原型与受控试用基础；真实长期效果与正式商业发布仍待验证。", 12, palette["muted"])
    save(fig, NAMES[0])

    fig, ax = canvas(1060, "MEMORIA  /  ENGINEERING ARCHITECTURE", "入口、账号、记忆与模型的边界",
                     "按账号组织运行时，连接原话证据、审核写入、适用状态与有界回答。")
    for i, (title, subtitle) in enumerate([
        ("Chat", "独立聊天界面 /"), ("Memory Studio", "资料与记忆后台 /studio/"),
        ("HTTP API", "接口契约与正式登录"), ("飞书私聊", "正式账号绑定后接入"),
    ]):
        x = 75 + i * 370
        box(ax, x, 200, 340, 90)
        text(ax, x + 22, 230, title, 19, weight="bold")
        text(ax, x + 22, 266, subtitle, 12, palette["muted"])
        arrow(ax, (x + 170, 295), (x + 170, 332))
    box(ax, 75, 336, 1450, 100, face=palette["ink"], edge=palette["ink"])
    text(ax, 106, 370, "身份、归属与独立运行时", 21, "white", "bold")
    text(ax, 106, 410, "网页 Cookie / Bearer · 正式渠道绑定 · 服务端账号 UUID · TenantRuntime", 14, "#CEE0F3")
    arrow(ax, (530, 442), (530, 473)); arrow(ax, (1275, 442), (1275, 473))
    box(ax, 75, 480, 915, 334, shadow=True)
    text(ax, 102, 514, "记忆读写与来源约束", 21, weight="bold")
    for y, prefix, labels in [
        (562, "写入", ["原话证据", "抽取候选", "明确审核", "记忆 / Claim"]),
        (678, "读取", ["适用状态 / 时间", "普通记忆混合检索", "预算内 Reader", "发布前依赖复核"]),
    ]:
        text(ax, 106, y - 19, prefix, 11, palette["muted"])
        for i, label in enumerate(labels):
            x = 103 + i * 216
            box(ax, x, y, 190, 52, face="#EEF5FA", radius=9)
            text(ax, x + 95, y + 26, label, 12, ha="center")
            if i < 3:
                arrow(ax, (x + 194, y + 26), (x + 212, y + 26))
    text(ax, 106, 633, "后台候选任务 · 来源 / 审核 / 版本事务一致", 11, palette["muted"])
    text(ax, 106, 779, "Claim 按槽位 / 条件访问；普通记忆使用 BM25、Dense 与 RRF。", 11, palette["muted"])
    box(ax, 1040, 480, 485, 150, shadow=True)
    text(ax, 1068, 514, "统一模型接口", 21, weight="bold")
    text(ax, 1068, 563, "main / fast：本地 Qwen 或远程模型", 13)
    text(ax, 1068, 603, "embedding：本地 BGE / 兼容服务", 13, palette["muted"])
    box(ax, 1040, 660, 485, 154, shadow=True)
    text(ax, 1068, 695, "工具与可选核验", 21, weight="bold")
    text(ax, 1068, 743, "MCP 白名单 · 操作授权 · 来源依赖", 13)
    text(ax, 1068, 785, "充分性 / 风险核验由显式开关启用", 12, palette["muted"])
    arrow(ax, (995, 567), (1035, 567), both=True)
    arrow(ax, (995, 739), (1035, 739), both=True)
    arrow(ax, (530, 820), (530, 861)); arrow(ax, (1275, 820), (1275, 861))
    box(ax, 75, 868, 1450, 114, face="#EAF1F8")
    for x, heading, body in [
        (102, "账号独立存储", "SQLite / WAL · FTS · 向量索引"),
        (582, "原话、状态与账本", "来源 · Claim版本 · 冻结研究记录"),
        (1062, "配置与模型边界", "账号凭据隔离 · 权重位于 model/"),
    ]:
        text(ax, x, 906, heading, 18, weight="bold")
        text(ax, x, 951, body, 12, palette["muted"])
    text(ax, 78, 1026, "依据现行源码与实现说明绘制；不同入口共用账号归属，图中不代表长期质量或商业验收已经完成。", 11, palette["muted"])
    save(fig, NAMES[1])
    files = {f"{name}.{ext}": digest(OUTPUT / f"{name}.{ext}") for name in NAMES for ext in ("png", "svg")}
    manifest = {"renderer": "scripts/render_project_diagrams.py", "renderer_sha256": digest(Path(__file__)),
                "font_family": font_manager.FontProperties(fname=font).get_name(),
                "architecture_source": "docs/implemented/系统架构与实现说明.md",
                "model_calls": 0, "new_scores": 0, "files": files}
    (OUTPUT / "project-diagrams.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                          encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        manifest = json.loads((OUTPUT / "project-diagrams.manifest.json").read_text(encoding="utf-8"))
        expected = {f"{name}.{ext}" for name in NAMES for ext in ("png", "svg")}
        if (digest(Path(__file__)) != manifest["renderer_sha256"]
                or set(manifest["files"]) != expected
                or manifest["model_calls"] != 0 or manifest["new_scores"] != 0
                or any(digest(OUTPUT / name) != value for name, value in manifest["files"].items())):
            raise ValueError("Project diagram renderer or asset fingerprint mismatch.")
        print("Verified four project diagram assets; no inference or scoring.")
    else:
        render()
        print("Rendered two project diagrams as PNG and outlined SVG; no inference or scoring.")


if __name__ == "__main__":
    main()
