"""Reproducible branded architecture and lifecycle diagrams; no model or private-data access."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'assets/images'
SOURCE = ROOT / 'docs/implemented/系统架构与实现说明.md'
MASCOT = ROOT / 'assets/brand/mascot-memory.png'
NAMES = ('memoria-architecture', 'memoria-overview')
COLORS = dict(bg='#0B122B', panel='#101C3B', line='#334978', text='#F6F8FF',
              muted='#AFBEDD', blue='#3187F8', cyan='#38D5F5', violet='#A68BFF')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_digest(path):
    return hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()


def render():
    os.environ.setdefault('MPLCONFIGDIR', str(ROOT / '.venv/.matplotlib-cache'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    from PIL import Image
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'svg.fonttype': 'path', 'svg.hashsalt': 'memoria-brand-v3'})
    for name in NAMES:
        architecture = name.endswith('architecture')
        width, height = 1800, 1300 if architecture else 920
        fig = plt.figure(figsize=(width/100, height/100), facecolor=COLORS['bg'])
        ax = fig.add_axes([0, 0, 1, 1]); ax.set(xlim=(0, width), ylim=(height, 0)); ax.axis('off')
        labels = []
        def text(x, y, value, size=23, color='text', bold=False, max_width=1660):
            label = ax.text(x, y, value, fontsize=size*.72, color=COLORS[color], va='center',
                            weight='bold' if bold else 'normal', zorder=5)
            labels.append((label, max_width))
        def box(x, y, w, h, color='line'):
            ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0,rounding_size=18',
                facecolor=COLORS['panel'], edgecolor=COLORS[color], linewidth=1.3, zorder=2))
        def arrow(x1, y1, x2, y2, color='cyan'):
            ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle='-|>', mutation_scale=15,
                linewidth=1.7, color=COLORS[color], zorder=3))
        def node(x, y, w, title, lines, color='blue', h=130):
            box(x, y, w, h, color); text(x+22, y+32, title, 25, color, True, w-44)
            for i, line in enumerate(lines): text(x+22, y+69+i*29, line, 20, 'muted', max_width=w-44)
        def row(y, entries, color='blue', arrows=False):
            w = (1672-28*(len(entries)-1))/len(entries)
            for i, (title, lines) in enumerate(entries):
                x = 64+i*(w+28); node(x, y, w, title, lines, color)
                if arrows and i < len(entries)-1: arrow(x+w+3, y+65, x+w+25, y+65, color)
        with Image.open(MASCOT) as image:
            image_height = 210 * image.height / image.width
            ax.imshow(image, extent=(1510, 1720, 18+image_height, 18), aspect='auto', zorder=4)
        text(64, 52, 'M E M O R I A', 26, 'cyan', True)
        text(64, 116, 'One memory kernel. Many ways to interact.' if architecture else 'How a source becomes a useful memory',
             43, bold=True, max_width=1400)
        text(64, 175, 'Evidence-aware formation, governed evolution, bounded answers.', 26, 'muted', max_width=1400)
        if architecture:
            text(64, 229, 'APPLICATIONS / SOURCE ADAPTERS', 18, 'blue', True)
            row(258, [('Chat + Memory Studio', ['Conversation / inspection']),
                      ('Feishu / QQ / Telegram', ['Explicit account bindings']),
                      ('Text / Word / task data', ['Role, author, source location']),
                      ('HTTP API / tools / MCP', ['Authenticated host adapters'])])
            arrow(900, 391, 900, 411)
            box(64, 415, 1672, 65, 'violet')
            text(90, 448, 'ACCOUNT UUID  /  TenantRuntime  /  shared MemoryKernel  /  isolated data + credentials', 26, 'violet', True, 1610)
            text(64, 520, 'MEMORY FORMATION + EVOLUTION', 18, 'violet', True)
            row(548, [('Evidence', ['Immutable source + quote', 'Role / subject / source clock']),
                      ('Observation + Pattern', ['Bounded historical context', 'Independent support review']),
                      ('Typed memory paths', ['Experiences / patterns / Claims', 'Separate admission policies']),
                      ('Governed evolution', ['Reinforce / revise / retract', 'Conditions + two time axes'])], 'violet', True)
            text(64, 719, 'UNIFIED ACCESS / DISTINCT DOMAIN RULES', 18, 'cyan', True)
            row(746, [('Claim / View', ['Current state / historical knowledge', 'Conditions, versions, pending conflicts']),
                      ('Ordinary memory', ['Keyword + BM25 + Dense -> RRF', 'Optional entities; live support groups']),
                      ('External source library', ['Source / card / consolidation retrieval', 'External authors never become user self'])], 'cyan')
            for x in (333, 900, 1467): arrow(x, 880, x, 910)
            box(64, 915, 1672, 96, 'blue')
            text(88, 946, 'Requirements -> bounded supplement -> complete evidence -> Reader -> support + version checks', 26, bold=True, max_width=1610)
            text(88, 982, 'Shared request / evidence budget. Valid citations or clarification / abstention. LLM reranking stays optional.', 22, 'muted', max_width=1610)
            arrow(900, 1015, 900, 1052)
            row(1058, [('Per-account persistence', ['SQLite / WAL / FTS / local vectors', 'Files, revisions and provenance']),
                       ('Model + tool services', ['DeepSeek / compatible LLM endpoints', 'Local BGE; MCP permission gates']),
                       ('Evaluation + observability', ['Native Kernel vs full-context baseline', 'Private raw ledger; public aggregates'])])
            text(64, 1245, 'IMPLEMENTED RESEARCH PROTOTYPE / Longitudinal quality and individual mechanism gains still require validation', 20, 'muted')
        else:
            row(270, [('01  Preserve', ['Conversation / document / task', 'Author, location, source clock']),
                      ('02  Form', ['Atomic observations', 'History is context, not proof']),
                      ('03  Consolidate', ['Cross-session / source support', 'Conditions and counterevidence']),
                      ('04  Publish', ['Independent LLM review', 'Risk / identity / source gates'])], 'violet', True)
            arrow(900, 405, 900, 480)
            box(64, 486, 1672, 156, 'cyan')
            text(94, 529, 'Accessible memory: experiences, conditional facts, source knowledge and task lessons', 29, 'cyan', True, 1600)
            text(94, 581, 'Retrieve -> select complete support groups -> answer with citations -> recheck live dependencies', 26, 'muted', max_width=1600)
            arrow(900, 647, 900, 692, 'violet')
            row(700, [('Continuous evolution', ['New evidence can reinforce, revise, supersede or retire memories.', 'Uncertain relations and high-risk changes remain reviewable.']),
                      ('Measure the whole lifecycle', ['Formation + retrieval + Reader + verification + judge cost.', 'No unsupported longitudinal or single-mechanism claims.'])], 'violet')
            text(64, 884, 'OWNER-PROVIDED BRAND KIT / Zero image-generation and research-model calls for these diagrams', 20, 'muted')
        fig.canvas.draw(); renderer = fig.canvas.get_renderer()
        for artist, max_width in labels:
            b = artist.get_window_extent(renderer)
            if b.x0 < 0 or b.y0 < 0 or b.x1 > width or b.y1 > height or b.width > max_width:
                raise ValueError('Diagram label overflow: '+artist.get_text())
        fig.savefig(OUTPUT/(name+'.png'), dpi=120, metadata={'Software': 'Memoria brand renderer'})
        fig.savefig(OUTPUT/(name+'.svg'), metadata={'Date': None, 'Creator': 'Memoria brand renderer'})
        svg = OUTPUT/(name+'.svg')
        svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n', encoding='utf-8', newline='\n')
        plt.close(fig)
    manifest = dict(renderer='scripts/render_project_diagrams.py', renderer_sha256=digest(Path(__file__)),
        architecture_source=SOURCE.relative_to(ROOT).as_posix(), architecture_source_sha256=source_digest(SOURCE),
        mascot_sha256=digest(MASCOT), language='en', svg_type='outlined text with embedded owner mascot',
        render_model_calls=0, research_model_calls=0, new_scores=0,
        files={name+'.'+ext: digest(OUTPUT/(name+'.'+ext)) for name in NAMES for ext in ('png', 'svg')})
    (OUTPUT/'project-diagrams.manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8', newline='\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--check', action='store_true')
    if parser.parse_args().check:
        m = json.loads((OUTPUT/'project-diagrams.manifest.json').read_text(encoding='utf-8'))
        assert digest(Path(__file__)) == m['renderer_sha256']
        assert source_digest(SOURCE) == m['architecture_source_sha256'] and digest(MASCOT) == m['mascot_sha256']
        assert all(digest(OUTPUT/name) == sha for name, sha in m['files'].items())
        print('Verified branded diagrams, renderer, mascot and implementation source.')
    else:
        render(); print('Rendered branded architecture and memory lifecycle; zero model calls.')


if __name__ == '__main__': main()
