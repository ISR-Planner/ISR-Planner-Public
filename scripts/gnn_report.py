#!/usr/bin/env python3
"""Render the GNN-vs-control comparison report from study outputs.

Consumes:
  - results/gnn_vs_llm_paired*.json   (paired replay of recorded LLM benchmark)
  - results/gnn_scaling.json          (two-axis scaling study)

Emits a markdown report and, when matplotlib is available, scaling charts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PLANNER_LABELS = {
    "reasoning": "Reasoning (LLM)",
    "langgraph_v4": "LangGraph v4 (LLM)",
    "crewai": "CrewAI (LLM)",
    "solver_control": "Solver control",
    "gnn": "GNN (allocation only)",
    "gnn_decoder": "GNN (learned decoder)",
}


def _fmt(value, spec="", dash="—"):
    if value is None:
        return dash
    if spec:
        try:
            return format(value, spec)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def render_paired(paired: dict) -> list[str]:
    agg = paired.get("aggregate", {})
    lines = [
        "## Paired comparison — identical environments",
        "",
        f"Every planner ran the same **{paired.get('runs')}** environments, replayed from "
        "a recorded benchmark batch (1 drone, 15 targets, seed 42). The LLM rows are the "
        "originally recorded results; the rest were computed by replay.",
        "",
        "| Planner | Mean latency | Valid-solution rate | Mean points (valid only) | Mean coverage |",
        "|---|---|---|---|---|",
    ]
    for key, label in PLANNER_LABELS.items():
        a = agg.get(key)
        if not a:
            continue
        lines.append(
            f"| {label} "
            f"| {_fmt(a.get('latency_s_mean'), '.3f')} s "
            f"| {_fmt((a.get('valid_solution_rate') or 0) * 100, '.0f')} % "
            f"| {_fmt(a.get('total_points_mean_valid_only'), '.1f')} "
            f"| {_fmt((a.get('target_coverage_mean') or 0) * 100, '.0f')} % |"
        )
    lines.append("")

    pv = paired.get("paired_points_valid_only") or {}
    if pv:
        lines += [
            "### Head-to-head on runs where both produced a *fuel-feasible* solution",
            "",
            "| Matchup | Runs compared | Wins | Losses | Ties | Mean point delta |",
            "|---|---|---|---|---|---|",
        ]
        for name, block in pv.items():
            if not block:
                continue
            a, b = name.split("_vs_")
            lines.append(
                f"| {PLANNER_LABELS.get(a, a)} vs {PLANNER_LABELS.get(b, b)} "
                f"| {block['compared_runs']} "
                f"| {block.get(f'{a}_wins', 0)} "
                f"| {block.get(f'{b}_wins', 0)} "
                f"| {block['ties']} "
                f"| {block['mean_point_delta']:+.2f} |"
            )
        lines.append("")
    return lines


def render_axis(summary: list[dict], axis: str, x_key: str, x_label: str) -> list[str]:
    if not summary:
        return []
    planners = ["solver_control", "gnn", "gnn_decoder"]
    lines = [
        f"### Axis: {axis}",
        "",
        f"| {x_label} | Matrix (s) | "
        + " | ".join(f"{PLANNER_LABELS[p]} total / routing (s)" for p in planners)
        + " | Points (control / decoder) |",
        "|---|---|" + "---|" * (len(planners) + 1),
    ]
    for point in summary:
        cells = []
        for p in planners:
            block = point.get(p) or {}
            if block.get("success_rate", 0) == 0:
                cells.append("**failed**")
            else:
                cells.append(
                    f"{_fmt(block.get('total_s_median'), '.2f')} / "
                    f"{_fmt(block.get('routing_s_median'), '.2f')}"
                )
        ctrl = (point.get("solver_control") or {}).get("points_median")
        dec = (point.get("gnn_decoder") or {}).get("points_median")
        lines.append(
            f"| {point[x_key]} | {_fmt(point.get('matrix_s_median'), '.2f')} | "
            + " | ".join(cells)
            + f" | {_fmt(ctrl, '.0f')} / {_fmt(dec, '.0f')} |"
        )
    lines.append("")
    return lines


def make_charts(scaling: dict, out_dir: Path) -> list[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []

    written = []
    axes = scaling.get("summary_by_axis", {})
    for axis_name, x_key, x_label in (
        ("drones", "drones", "Drones"),
        ("targets_per_drone", "targets", "Targets (1 drone)"),
    ):
        summary = axes.get(axis_name)
        if not summary:
            continue
        xs = [p[x_key] for p in summary]
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))

        for planner, style in (
            ("solver_control", "o-"),
            ("gnn", "s--"),
            ("gnn_decoder", "^-"),
        ):
            ys = [(p.get(planner) or {}).get("routing_s_median") for p in summary]
            ax1.plot(xs, ys, style, label=PLANNER_LABELS[planner])
            pts = [(p.get(planner) or {}).get("points_median") for p in summary]
            ax2.plot(xs, pts, style, label=PLANNER_LABELS[planner])

        ax1.plot(
            xs, [p.get("matrix_s_median") for p in summary], "k:",
            label="SAM distance matrix (shared)",
        )
        ax1.set_xlabel(x_label)
        ax1.set_ylabel("Routing time (s, median)")
        ax1.set_yscale("log")
        ax1.set_title(f"Cost vs {x_label.lower()}")
        ax1.legend(fontsize=8)
        ax1.grid(alpha=0.3)

        ax2.set_xlabel(x_label)
        ax2.set_ylabel("Points (median)")
        ax2.set_title(f"Solution quality vs {x_label.lower()}")
        ax2.legend(fontsize=8)
        ax2.grid(alpha=0.3)

        fig.tight_layout()
        path = out_dir / f"gnn_scaling_{axis_name}.png"
        fig.savefig(path, dpi=130)
        plt.close(fig)
        written.append(str(path))
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--paired", default="results/gnn_vs_llm_paired.json")
    p.add_argument("--scaling", default="results/gnn_scaling.json")
    p.add_argument("--out", default="results/GNN_COMPARISON_REPORT.md")
    args = p.parse_args()

    lines = [
        "# GNN planner vs. existing planners",
        "",
        "Generated by `scripts/gnn_report.py`. The control is the deterministic "
        "allocator + exact Held-Karp orienteering solver shipped on `main`.",
        "",
    ]

    paired_path = Path(args.paired)
    if paired_path.exists():
        lines += render_paired(json.loads(paired_path.read_text()))
    else:
        lines += [f"_No paired results at `{paired_path}`._", ""]

    scaling_path = Path(args.scaling)
    if scaling_path.exists():
        scaling = json.loads(scaling_path.read_text())
        lines += [
            "## Scaling study",
            "",
            "`routing` is total time minus the shared SAM-aware distance-matrix build. "
            "Both planners pay the matrix cost, so only the routing column reflects work "
            "the GNN can actually displace.",
            "",
        ]
        axes = scaling.get("summary_by_axis", {})
        lines += render_axis(axes.get("drones", []), "drones (targets fixed)", "drones", "Drones")
        lines += render_axis(
            axes.get("targets_per_drone", []), "targets per drone (1 drone)", "targets", "Targets"
        )
        charts = make_charts(scaling, Path(args.out).parent)
        if charts:
            lines += ["### Charts", ""]
            lines += [f"![{Path(c).stem}]({Path(c).name})" for c in charts]
            lines.append("")
    else:
        lines += [f"_No scaling results at `{scaling_path}`._", ""]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"wrote {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
