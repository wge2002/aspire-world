"""Plot independently audited scene-world development results; no simulator calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np


def render(audit_path, output):
    data = json.loads(audit_path.read_text())
    if not data["complete"]:
        raise ValueError("only a complete independent audit can be plotted")
    output.mkdir(parents=True, exist_ok=False)
    rows = data["rows"]
    seeds = [r["seed"] for r in rows]
    colors = ["#24678D", "#CB6842"]
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "savefig.facecolor": "white"})
    fig, axes = plt.subplots(2, 1, figsize=(11.5, 7.8), gridspec_kw={"height_ratios": [1, 1.15]})
    ax = axes[0]
    for index, label in [(0, "Initial grasp check"), (1, "After recovery")]:
        x, y = [], []
        for row in rows:
            if len(row["comparisons"]) > index:
                value = row["comparisons"][index]["error_m"]
                if value is not None:
                    x.append(row["seed"] + (index - .5) * .18)
                    y.append(value * 100)
        ax.scatter(x, y, label=label, s=48, color=colors[index],
                   marker="o" if index == 0 else "s", zorder=3)
    ax.axhline(3, color="#49604E", ls="--", label="Frozen tolerance: 3 cm")
    maximum = max(c["error_m"] * 100 for r in rows for c in r["comparisons"] if c["error_m"] is not None)
    ax.set(xlim=(50.5, 65.5), ylim=(0, maximum + 8), xticks=seeds,
           ylabel="XYZ prediction residual (cm)", xlabel="Development seed")
    ax.grid(axis="y", color="#E6E8EB", zorder=0)
    ax.legend(loc="upper right", ncol=3, frameon=False, fontsize=9)
    ax.set_title("Object prediction versus a later public RGB-D measurement", loc="left", pad=14)
    ids = list(rows[0]["anchor_measurements"])
    known = np.array([[r["anchor_measurements"][oid]["status"] == "ok" for r in rows] for oid in ids])
    ax = axes[1]
    ax.imshow(known, cmap=ListedColormap(["#E8EBEF", "#24678D"]), vmin=0, vmax=1,
              aspect="auto", interpolation="nearest")
    ax.set(xticks=range(len(seeds)), xticklabels=seeds, yticks=range(len(ids)),
           yticklabels=ids, xlabel="Development seed")
    ax.set_xticks(np.arange(-.5, len(seeds), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(ids), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.5)
    ax.tick_params(which="minor", bottom=False, left=False)
    ax.set_title("Initial scene anchoring: blue = measured; gray = unknown", loc="left", pad=14)
    fig.suptitle(f"Frozen Opus 4.6 scene world | bowl on plate | {data['successes']}/{data['planned']} task successes",
                 fontsize=15, x=.04, ha="left", y=.98)
    fig.text(.04, .016, "Single new arm; seeds 51–65. Model requests: 0. Residual agreement is not a grasp-success label.",
             fontsize=9, color="#555E68")
    fig.tight_layout(rect=(.025, .04, .99, .94), h_pad=2)
    for extension in ["png", "pdf"]:
        fig.savefig(output / ("scene-world-results." + extension), dpi=180)
    plt.close(fig)
    print(json.dumps({"audit": str(audit_path), "figures": [str(p) for p in output.iterdir()]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.audit, args.output)
