"""Render public historical aggregates; never open private data, gold or models."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/assets/evaluation/registered-20261012.json"
CHARTS = ("source-recall", "qa-literal-and-citations", "empty-claim-access")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(data):
    protocol = json.loads((ROOT / data["registration"]).read_text(encoding="utf-8"))
    assert data["evaluation_id"] == protocol["evaluation_id"]
    assert data["protocol_id"] == protocol["protocol_id"]
    assert data["queries_per_run"] == protocol["expected_cases"]
    assert data["reader_calls"] == protocol["planned_reader_calls"]
    assert data["reader_model"] == protocol["reader_model"]
    report = (ROOT / data["source_report"]).read_text(encoding="utf-8")
    assert data["measured_code_commit"] in report
    total = data["queries_per_run"] * data["repetitions_per_condition"]
    assert data["answerable_queries"] + data["unanswerable_queries"] == data["queries_per_run"]
    assert sum(r["total"] for r in data["retrieval"]) == data["retrieval_case_runs"]
    assert sum(r["total"] for r in data["qa"]) == data["reader_calls"]
    for row in data["retrieval"]:
        assert row["total"] == total and row["known_total"] == data["answerable_queries"] * data["repetitions_per_condition"]
        assert round(100 * row["known_recalled"] / row["known_total"], 2) == row["recall_percent"]
        assert f'{row["p50_ms"]:.3f}/{row["p95_ms"]:.3f}' in report
    for row in data["qa"]:
        assert row["total"] == total
        assert round(100 * row["literal_matches"] / row["total"], 2) == row["literal_percent"]
        assert f'{row["literal_matches"]}/{row["total"]}' in report
        assert f'{row["known_citation_recall_percent"]:.2f}%' in report
        assert f'{row["case_p50_ms"]}/{row["case_p95_ms"]}' in report
    assert sum(r["failures"] for r in data["qa"]) == data["qa_failures"]["total"]
    assert sum(data["qa_failures"][k] for k in ("unseen_citation", "provider_error")) == data["qa_failures"]["total"]
    assert data["usage"]["known_calls"] + data["usage"]["unknown_calls"] == data["reader_calls"]
    access = data["empty_claim_access"]
    assert access["approved_claims"] == 0 and access["run_id"] in report
    assert sum(r["calls"] for r in access["conditions"]) == access["total_accesses"]
    for row in access["conditions"]:
        assert f'{row["p50_ms"]}/{row["p95_ms"]}' in report
    assert all(v is None for v in data["unmeasured"].values())


def render(data, output):
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".venv/.matplotlib-cache"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":11, "axes.spines.top":False,
                         "axes.spines.right":False, "svg.hashsalt":"memoria-public-eval-v1"})
    colors = ["#386b53", "#b1baa7", "#657daf", "#ba8850"]
    output.mkdir(parents=True, exist_ok=True)

    def setup(title, subtitle, footnote):
        fig, ax = plt.subplots(figsize=(10.5, 5.4))
        fig.subplots_adjust(left=.09, right=.97, bottom=.23, top=.77)
        fig.text(.09, .94, title, fontsize=19, fontweight="bold", color="#243e32")
        fig.text(.09, .865, subtitle, fontsize=10, color="#68796e")
        fig.text(.09, .06, footnote, fontsize=9, color="#66756b", linespacing=1.5)
        ax.set_axisbelow(True)
        ax.yaxis.grid(True, alpha=.18)
        return fig, ax

    def save(fig, name):
        fig.savefig(output / (name + ".png"), dpi=160, metadata={"Software":"Memoria public aggregate renderer"})
        svg = output / (name + ".svg")
        fig.savefig(svg, metadata={"Date":None, "Creator":"Memoria public aggregate renderer"})
        # Matplotlib emits trailing spaces in paths; normalize without changing geometry.
        svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines())+"\n",
                       encoding="utf-8", newline="\n")
        plt.close(fig)

    rows = data["retrieval"]
    fig, ax = setup("Known-source evidence recall", "2026-10-12 | registered code 040427c | 3 runs per method | 41 answerable queries per run",
                    "50 anonymous source groups, 206 utterances; these are not continuous users.\nWindow also covers all known evidence. No longitudinal benefit is established.")
    ax.bar([r["label"] for r in rows], [r["recall_percent"] for r in rows], color=colors, width=.58)
    ax.set_ylim(0, 116)
    ax.set_yticks(range(0, 101, 20)); ax.yaxis.set_major_formatter(PercentFormatter(100))
    ax.set_ylabel("Evidence recall on answerable query runs")
    for i, row in enumerate(rows):
        ax.text(i, row["recall_percent"]+3, f'{row["recall_percent"]:.2f}%\n{row["known_recalled"]}/{row["known_total"]}', ha="center", fontsize=10)
    save(fig, CHARTS[0])

    rows = data["qa"]
    fig, ax = setup("QA literal match and cited-evidence recall", "2026-10-12 | DeepSeek reader | 192 query runs per method | failures stay in the denominator",
                    "AI-assisted development gold; literal match is not human semantic accuracy.\nExecution failures: Window 3, Dense 5, RRF 7. Compact FTS QA was not run in this batch.")
    x = list(range(len(rows))); width = .34
    left = ax.bar([n-width/2 for n in x], [r["literal_percent"] for r in rows], width, color=colors[0], label="Literal match (all query runs)")
    right = ax.bar([n+width/2 for n in x], [r["known_citation_recall_percent"] for r in rows], width, color=colors[2], label="Citation recall (answerable query runs)")
    ax.set_xticks(x, [r["label"] for r in rows]); ax.set_ylim(0, 114)
    ax.set_yticks(range(0, 101, 20)); ax.yaxis.set_major_formatter(PercentFormatter(100))
    for bars in (left, right):
        ax.bar_label(bars, labels=[f'{bar.get_height():.2f}%' for bar in bars], padding=4, fontsize=10)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02), ncol=2, frameon=False, fontsize=9)
    save(fig, CHARTS[1])

    rows = data["empty_claim_access"]["conditions"]
    fig, ax = setup("Empty Claim-state access overhead", "0 approved Claims | database copy | 1,800 core-function calls per condition",
                    "Every response was clarify. The empty cache adds overhead in this measurement.\nNo positive-profile view benefit was measured; these timings are not HTTP or production SLOs.")
    x = list(range(len(rows)))
    bars50 = ax.bar([n-width/2 for n in x], [r["p50_ms"] for r in rows], width, label="p50", color=colors[0])
    bars95 = ax.bar([n+width/2 for n in x], [r["p95_ms"] for r in rows], width, label="p95", color=colors[3])
    ax.set_xticks(x, [r["label"] for r in rows]); ax.set_ylim(0, .27); ax.set_ylabel("Elapsed wall time (ms)")
    for bars in (bars50, bars95):
        ax.bar_label(bars, labels=[f'{bar.get_height():.3f}' for bar in bars], padding=4, fontsize=10)
    ax.legend(loc="upper left", frameon=False)
    save(fig, CHARTS[2])
    files = {name+ext:{"sha256":digest(output/(name+ext)), "bytes":(output/(name+ext)).stat().st_size}
             for name in CHARTS for ext in (".png", ".svg")}
    (output / "figures.manifest.json").write_text(json.dumps({"source_summary_sha256":digest(DEFAULT),
                "matplotlib":matplotlib.__version__, "measured_commit":data["measured_code_commit"],
                "new_model_calls":0, "new_scoring":False, "files":files}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify public data and previously rendered files; no model calls")
    args = parser.parse_args()
    data = json.loads(DEFAULT.read_text(encoding="utf-8")); validate(data)
    output = DEFAULT.parent
    if args.check:
        manifest = json.loads((output/"figures.manifest.json").read_text(encoding="utf-8"))
        assert manifest["source_summary_sha256"] == digest(DEFAULT)
        assert not manifest["new_scoring"] and manifest["new_model_calls"] == 0
        for name, meta in manifest["files"].items():
            assert digest(output/name) == meta["sha256"] and (output/name).stat().st_size == meta["bytes"]
        print("Verified public protocol, report aggregates and 6 figure fingerprints; no private input or inference")
    else:
        render(data, output)
        print("Rendered 3 PNG/SVG charts from the historical public summary; 0 model calls, 0 new scoring")


if __name__ == "__main__":
    main()
