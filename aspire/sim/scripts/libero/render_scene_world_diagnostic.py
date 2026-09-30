"""Render recorded grounding mismatch and explicitly synthetic code behavior."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from PIL import Image


def render(root):
    output = root / "model-figures"
    output.mkdir(exist_ok=True)
    image = Image.open(root / "input-rgb.jpg")
    diagnostic = json.loads((root / "model-audit/candidate-boxes.json").read_text())
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 5.5), layout="constrained")
    for ax, row in zip(axes, diagnostic["rows"]):
        ax.imshow(image)
        candidate = max(row["candidates"], key=lambda c: c["score"])
        for box, color, style, label in [
            (row["inventory_box"], "#ef8a24", "--", "Model inventory box"),
            (candidate["box"], "#008778", "-", "SAM3 highest-score box")]:
            x1, y1, x2, y2 = box
            ax.add_patch(Rectangle((x1, y1), x2-x1, y2-y1,
                                   linewidth=2.7, edgecolor=color, facecolor="none",
                                   linestyle=style, label=label))
        ax.set_xlim(280, 610)
        ax.set_ylim(440, 190)
        ax.axis("off")
        ax.set_title(f"{row['id']}: SAM3 score {candidate['score']:.3f}, IoU = 0", fontsize=12)
        ax.legend(loc="lower left", fontsize=9, framealpha=.94)
    fig.suptitle("Object identity mismatch before world initialization", fontsize=16)
    fig.supxlabel("Same saved RGB and unchanged labels. Both masks were rejected by the original IoU gate.\nCandidate inspection used 2 separate local perception calls; original results remain unchanged.", fontsize=10)
    path = output / "grounding-diagnostic.png"
    assert not path.exists()
    fig.savefig(path, dpi=160, bbox_inches="tight", pad_inches=.2)
    plt.close(fig)

    control = json.loads((root / "model-audit/grounding-control/control-report.json").read_text())
    rows = control["steps"]
    base_z = rows[0]["prediction"]["objects"]["bowl"]["position"][2]
    heights = [(r["prediction"]["objects"]["bowl"]["position"][2]-base_z)*100 for r in rows]
    fig, ax = plt.subplots(figsize=(10.5, 5.0), layout="constrained")
    ax.axhline(0, color="#657786", linestyle="--", linewidth=1.5, label="Stationary-object hypothesis")
    ax.plot(range(len(rows)), heights, color="#2264a8", marker="o", linewidth=2.5,
            label="Generated program's nominal prediction")
    ax.scatter([3], [0], s=130, facecolors="none", edgecolors="#d2513c", linewidths=2, zorder=4)
    ax.annotate("Synthetic evidence fits the position,\nbut attachment stays conditional",
                xy=(3, 0), xytext=(2.6, 5.1), fontsize=10,
                arrowprops={"arrowstyle": "->", "color": "#d2513c"})
    ax.set_xticks(range(len(rows)), ["Close near\nbowl", "Measured\nmove", "Unknown\nevidence",
                                     "Stationary\nevidence", "Next\nmove", "Open\ngripper"])
    ax.set_ylabel("Predicted bowl Z relative to initial measurement (cm)")
    ax.set_ylim(-1.5, 16)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper left", frameon=False, fontsize=10)
    ax.set_title("Synthetic code probe: updating a position does not revise the attachment hypothesis", fontsize=12)
    fig.supxlabel("Same unedited scene program; bowl/plate measurements reused from the manual control.\nThese are declared synthetic inputs, not a robot trajectory or measured physical errors.", fontsize=10)
    path = output / "synthetic-hypothesis-probe.png"
    assert not path.exists()
    fig.savefig(path, dpi=160, bbox_inches="tight", pad_inches=.2)
    plt.close(fig)
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    render(parser.parse_args().root)
