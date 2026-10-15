"""Render the English architecture and package the approved ImageGen poster.

The poster PNG is an authored input, never regenerated or replaced by this CLI.
Its SVG is a portable raster wrapper; the architecture SVG has outlined text.
No service-model inference, private-data access or evaluation runs here.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets/images"
NAMES = ("memoria-overview", "memoria-architecture")
PROMPT = OUTPUT / "memoria-overview.prompt.txt"
SOURCE = ROOT / "docs/implemented/系统架构与实现说明.md"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_digest(path):
    # Markdown checkout line endings vary; hash canonical UTF-8 / LF text.
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def write_text(path, content):
    path.write_text(content, encoding="utf-8", newline="\n")


def render():
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".venv/.matplotlib-cache"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Circle, PathPatch
    from matplotlib.path import Path as VectorPath
    from PIL import Image

    poster = OUTPUT / "memoria-overview.png"
    if not poster.is_file() or not PROMPT.is_file():
        raise FileNotFoundError("Keep the approved poster PNG and its prompt before rendering.")
    with Image.open(poster) as source_image:
        source_image.verify()
    with Image.open(poster) as source_image:
        poster_width, poster_height = source_image.size
    encoded = base64.b64encode(poster.read_bytes()).decode("ascii")
    write_text(OUTPUT / "memoria-overview.svg", (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{poster_width}" height="{poster_height}" '
        f'viewBox="0 0 {poster_width} {poster_height}">\n'
        '<title>Memoria — Memory that stays in context.</title>\n'
        '<desc>English ImageGen promotional poster. Embedded raster, not vector artwork.</desc>\n'
        f'<image width="{poster_width}" height="{poster_height}" '
        f'href="data:image/png;base64,{encoded}"/>\n</svg>\n'
    ))

    font_family = None
    for candidate in ("Segoe UI", "Inter", "DejaVu Sans"):
        try:
            regular = font_manager.findfont(candidate, fallback_to_default=False)
            font_family = candidate
            break
        except ValueError:
            continue
    if font_family is None:
        raise RuntimeError("Install Segoe UI, Inter or DejaVu Sans to render the architecture.")
    bold = font_manager.findfont(
        font_manager.FontProperties(family=font_family, weight="bold"), fallback_to_default=False
    )
    plt.rcParams.update({"svg.fonttype": "path", "svg.hashsalt": "memoria-english-v2"})
    colors = {
        "bg": "#080F20", "panel": "#101C31", "node": "#15243B", "edge": "#2B3B55",
        "white": "#EDF2FF", "muted": "#A7B6D2", "faint": "#788BAE",
        "lavender": "#B4A8FF", "cyan": "#76DAEB", "mint": "#9EE5C9",
    }
    width, height = 1800, 1380
    fig = plt.figure(figsize=(width / 100, height / 100), facecolor=colors["bg"])
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    labels = []

    def text(x, y, content, size=24, color="white", weight="normal", align="left", max_width=None):
        artist = ax.text(
            x, y, content, color=colors.get(color, color), ha=align, va="center",
            fontproperties=font_manager.FontProperties(
                fname=bold if weight == "bold" else regular, size=size * 72 / 100
            ), linespacing=1.25, zorder=5,
        )
        labels.append((artist, max_width))
        return artist

    def box(x, y, w, h, face="panel", edge="edge", radius=18, dashed=False):
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
            facecolor=colors.get(face, face), edgecolor=colors.get(edge, edge),
            lw=1.2, linestyle=(0, (5, 4)) if dashed else "solid", zorder=2,
        ))

    def line(points, color="edge", lw=1.5):
        xs, ys = zip(*points)
        ax.plot(xs, ys, color=colors.get(color, color), lw=lw, zorder=3,
                solid_capstyle="round", solid_joinstyle="round")

    def arrow(start, end, color="faint", both=False):
        ax.add_patch(FancyArrowPatch(
            start, end, arrowstyle="<->" if both else "-|>", mutation_scale=13,
            lw=1.6, color=colors[color], zorder=3, shrinkA=0, shrinkB=0,
        ))

    def pill(x, y, w, label, color="lavender"):
        box(x, y, w, 28, face="node", edge="edge", radius=14)
        text(x + w / 2, y + 14, label, 15, color, "bold", "center", w - 14)

    def icon(x, y, kind, color="lavender"):
        # A consistent line-icon vocabulary; no external icon assets.
        if kind == "chat":
            vertices = [(x, y), (x + 28, y), (x + 28, y + 20), (x + 12, y + 20),
                        (x + 5, y + 27), (x + 5, y + 20), (x, y + 20), (x, y)]
            ax.add_patch(PathPatch(VectorPath(vertices), fill=False, lw=1.7,
                                   edgecolor=colors[color], zorder=4))
        elif kind == "studio":
            box(x, y, 28, 26, face="panel", edge=color, radius=4)
            line([(x + 9, y + 1), (x + 9, y + 25)], color)
            line([(x + 14, y + 9), (x + 23, y + 9)], color)
            line([(x + 14, y + 16), (x + 23, y + 16)], color)
        elif kind == "api":
            line([(x + 8, y + 3), (x, y + 12), (x + 8, y + 21)], color)
            line([(x + 22, y + 3), (x + 30, y + 12), (x + 22, y + 21)], color)
            line([(x + 18, y), (x + 12, y + 25)], color)
        elif kind == "channel":
            line([(x + 4, y + 5), (x + 24, y + 5), (x + 14, y + 24), (x + 4, y + 5)], color)
            for cx, cy in ((x + 4, y + 5), (x + 24, y + 5), (x + 14, y + 24)):
                ax.add_patch(Circle((cx, cy), 3, facecolor=colors[color], zorder=4))
        elif kind == "lock":
            box(x, y + 12, 29, 25, face="#20233E", edge=color, radius=5)
            line([(x + 6, y + 12), (x + 6, y + 5), (x + 10, y),
                  (x + 19, y), (x + 23, y + 5), (x + 23, y + 12)], color, 1.8)
            line([(x + 14.5, y + 22), (x + 14.5, y + 29)], color, 1.8)

    text(72, 53, "M E M O R I A", 25, "lavender", "bold")
    text(1728, 53, "SYSTEM ARCHITECTURE", 18, "muted", align="right")
    text(72, 125, "How Memoria works", 60, weight="bold")
    text(74, 184, "Evidence in. Applicable memory. Bounded answers.", 27, "muted")
    line([(72, 211), (1728, 211)])

    entries = (
        ("Chat", "Independent conversation UI  /", "chat"),
        ("Memory Studio", "Review & sources  /studio/", "studio"),
        ("HTTP API", "Authenticated service endpoints", "api"),
        ("Feishu", "Account-bound private messages", "channel"),
    )
    for i, (title, subtitle, kind) in enumerate(entries):
        x = 72 + 420 * i
        box(x, 238, 396, 103, dashed=i == 3)
        icon(x + 22, 261, kind)
        text(x + 70, 276, title, 29, weight="bold", max_width=304)
        text(x + 22, 316, subtitle, 21, "muted", max_width=350)
        if i == 3:
            pill(x + 261, 253, 113, "OPTIONAL")
        arrow((x + 198, 345), (x + 198, 374), "lavender")

    box(72, 375, 1656, 97, face="#20233E", edge="#4C4775")
    icon(99, 398, "lock")
    text(150, 410, "Identity & tenant gateway", 30, weight="bold")
    text(150, 446, "Login / Cookie / Bearer  →  Account UUID  →  Isolated TenantRuntime",
         23, "muted", max_width=1320)
    pill(1543, 395, 157, "PER ACCOUNT")
    arrow((637, 474), (637, 514), "lavender")

    # Service dependencies connect to the runtime, not directly to identity.
    box(72, 515, 1130, 641)
    text(100, 554, "Memory runtime", 34, weight="bold")
    text(1174, 553, "SOURCE + VERSION CHECKS", 16, "faint", align="right")
    text(100, 606, "01   WRITE / APPROVAL-GATED", 19, "lavender", "bold")
    write_nodes = (
        ("Original", "evidence"), ("Candidate", "extraction"),
        ("Explicit", "review"), ("Memory /", "Claim"),
    )
    for i, (first, second) in enumerate(write_nodes):
        x = 100 + i * 274
        box(x, 632, 252, 105, face="node", edge="#3A4163", radius=12)
        text(x + 20, 665, first, 25, weight="bold", max_width=212)
        text(x + 20, 699, second, 25, "lavender", max_width=212)
        if i < 3:
            arrow((x + 256, 684), (x + 270, 684), "lavender")
    text(100, 769, "Background workers · Atomic source / review / revision updates", 21, "muted",
         max_width=1074)
    line([(100, 796), (1174, 796)])

    text(100, 829, "02   READ / ROUTE BY QUESTION", 19, "cyan", "bold")
    for x, w, title, subtitle in (
        (100, 515, "Claims & conditions", "Typed slots · current / history / clarify"),
        (643, 531, "Ordinary memories", "BM25 + Dense → RRF · chunked evidence"),
    ):
        box(x, 855, w, 81, face="#102A36", edge="#2C606B", radius=12)
        text(x + 20, 881, title, 26, "cyan", "bold", max_width=w - 40)
        text(x + 20, 913, subtitle, 21, "muted", max_width=w - 40)
    # Parallel retrieval branches, not Claim -> vector retrieval.
    line([(357.5, 937), (357.5, 950), (226, 950)], "cyan")
    line([(908.5, 937), (908.5, 950), (226, 950)], "cyan")
    arrow((226, 950), (226, 970), "cyan")
    read_nodes = (
        ("Evidence budget", "Layered context"), ("Reader + tools", "Bounded prompt"),
        ("Version recheck", "Before publication"), ("Answer", "Sources + trace"),
    )
    for i, (title, subtitle) in enumerate(read_nodes):
        x = 100 + i * 274
        box(x, 971, 252, 93, face="#122634" if i < 3 else "#13332F",
            edge="#294F5F" if i < 3 else "#39715F", radius=12)
        text(x + 18, 1002, title, 24, "white" if i < 3 else "mint", "bold", max_width=216)
        text(x + 18, 1036, subtitle, 21, "muted", max_width=216)
        if i < 3:
            arrow((x + 256, 1017), (x + 270, 1017), "cyan")
    text(100, 1115, "Claims stay structured; they are not embedded in the memory vector index.",
         21, "muted", max_width=1074)

    sidebar = 1254
    box(sidebar, 515, 474, 190)
    text(sidebar + 26, 554, "Unified model interface", 29, weight="bold", max_width=422)
    text(sidebar + 26, 594, "MAIN / FAST", 17, "lavender", "bold")
    text(sidebar + 26, 626, "Local Qwen / remote API", 25, max_width=422)
    text(sidebar + 26, 674, "BGE / compatible embeddings", 23, "muted", max_width=422)
    arrow((1208, 620), (1247, 620), "lavender", both=True)

    box(sidebar, 730, 474, 170)
    text(sidebar + 26, 770, "Tools & MCP", 29, weight="bold", max_width=422)
    text(sidebar + 26, 817, "ToolRegistry / MCP host", 25, max_width=422)
    text(sidebar + 26, 864, "Allowlist · auth · timeouts", 23, "muted", max_width=422)
    arrow((1208, 816), (1247, 816), "lavender", both=True)

    box(sidebar, 925, 474, 175, edge="#554A77", dashed=True)
    text(sidebar + 26, 965, "Optional verification", 29, weight="bold", max_width=422)
    text(sidebar + 26, 1011, "Sufficiency / risk checks", 25, max_width=422)
    text(sidebar + 26, 1058, "Enabled by an explicit option", 23, "muted", max_width=422)
    arrow((1208, 1017), (1247, 1017), "lavender", both=True)
    text(sidebar + 26, 1135, "Services used by the runtime", 20, "faint", max_width=422)

    arrow((637, 1158), (637, 1191), "cyan", both=True)
    box(72, 1197, 1656, 113, face="#111D32")
    storage = (
        (100, "Per-account storage", "SQLite / WAL · FTS · vectors"),
        (653, "Sources & revisions", "Original evidence · Claim history"),
        (1206, "Trace & research", "Usage / retries / run fingerprints"),
    )
    for x, title, subtitle in storage:
        text(x, 1235, title, 27, weight="bold", max_width=490)
        text(x, 1280, subtitle, 22, "muted", max_width=490)
    for x in (625, 1178):
        line([(x, 1219), (x, 1289)])
    text(74, 1346, "Current implementation · Research prototype", 19, "faint")
    text(1728, 1346, "PRIVATE CONFIG / CREDENTIALS / LOCAL MODEL WEIGHTS", 16, "faint", align="right")

    # Check actual glyph extents before exporting to catch clipping and overflow.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for artist, max_width in labels:
        bounds = artist.get_window_extent(renderer)
        if bounds.x0 < 0 or bounds.y0 < 0 or bounds.x1 > width or bounds.y1 > height:
            raise ValueError(f"Text extends outside canvas: {artist.get_text()}")
        if max_width is not None and bounds.width > max_width:
            raise ValueError(f"Text exceeds its allocated width: {artist.get_text()}")
    fig.savefig(OUTPUT / "memoria-architecture.png", dpi=160,
                metadata={"Software": "Memoria English architecture renderer"})
    svg = OUTPUT / "memoria-architecture.svg"
    fig.savefig(svg, metadata={"Date": None, "Creator": "Memoria English architecture renderer"})
    write_text(svg, "\n".join(row.rstrip() for row in svg.read_text(encoding="utf-8").splitlines()) + "\n")
    plt.close(fig)

    manifest = {
        "renderer": "scripts/render_project_diagrams.py", "renderer_sha256": digest(Path(__file__)),
        "language": "en", "font_family": font_family, "architecture_source": SOURCE.relative_to(ROOT).as_posix(),
        "architecture_source_sha256": source_digest(SOURCE), "architecture_source_hash_format": "utf-8-lf",
        "poster": {"creation_tool": "built-in image_gen", "image_generation_calls": 2,
                   "prompt": PROMPT.relative_to(ROOT).as_posix(), "prompt_sha256": digest(PROMPT),
                   "svg_type": "embedded-raster", "size": [poster_width, poster_height]},
        "architecture": {"svg_type": "outlined-vector", "size": [2880, 2208],
                         "design": "English layered flow / parallel Claim and ordinary-memory access"},
        "render_model_calls": 0, "research_model_calls": 0, "new_scores": 0,
        "files": {f"{name}.{ext}": digest(OUTPUT / f"{name}.{ext}") for name in NAMES for ext in ("png", "svg")},
    }
    write_text(OUTPUT / "project-diagrams.manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        manifest = json.loads((OUTPUT / "project-diagrams.manifest.json").read_text(encoding="utf-8"))
        expected = {f"{name}.{ext}" for name in NAMES for ext in ("png", "svg")}
        if (digest(Path(__file__)) != manifest["renderer_sha256"]
                or set(manifest["files"]) != expected
                or manifest["language"] != "en"
                or source_digest(SOURCE) != manifest["architecture_source_sha256"]
                or digest(PROMPT) != manifest["poster"]["prompt_sha256"]
                or any(manifest[key] != 0 for key in ("render_model_calls", "research_model_calls", "new_scores"))
                or any(digest(OUTPUT / name) != value for name, value in manifest["files"].items())):
            raise ValueError("Project diagram source, renderer or asset fingerprint mismatch.")
        print("Verified English poster, raster SVG wrapper, vector architecture and source fingerprints.")
    else:
        render()
        print("Rendered English vector architecture; preserved ImageGen poster and packaged its SVG wrapper.")


if __name__ == "__main__":
    main()
