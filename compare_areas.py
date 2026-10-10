"""Phase 5: compare street shade through the day across every built area.

Usage (after build_area.py):  python compare_areas.py
Prints a table and saves data/compare_areas.png.
"""
import json
import os

import matplotlib.pyplot as plt

from areas import AREAS, HOURS

# Colours follow each area's position in areas.py, so an area keeps its colour
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, INK_2, GRID, AXIS = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"


def hour_label(h):
    return f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"


def load_summaries():
    built = []
    for i, key in enumerate(AREAS):
        path = os.path.join("data", key, "summary.json")
        if not os.path.exists(path):
            continue
        if i >= len(COLORS):
            print(f"Skipping {key}: only the first {len(COLORS)} areas get a colour")
            continue
        with open(path, encoding="utf-8") as f:
            summary = json.load(f)
        summary["color"] = COLORS[i]
        built.append(summary)
    return built


def main():
    built = load_summaries()
    if not built:
        print("No areas built yet. Run: python build_area.py --all")
        return

    # The table view: the same numbers as the chart
    noon = HOURS.index(12)
    print(f"\n{'Area':34} {'8am':>5} {'noon':>5} {'6pm':>5}   lowest")
    for s in built:
        v = s["hourly_shade"]
        low = min(range(len(v)), key=lambda i: v[i])
        print(f"{s['name']:34} {v[0]:5.0%} {v[noon]:5.0%} {v[-1]:5.0%}   "
              f"{v[low]:.0%} at {hour_label(HOURS[low])}")

    labels = [hour_label(h) for h in HOURS]
    fig, ax = plt.subplots(figsize=(10, 5.6))
    for s in built:
        ax.plot(labels, [x * 100 for x in s["hourly_shade"]], color=s["color"],
                linewidth=2, marker="o", markersize=5, label=s["name"])

    least = min(built, key=lambda s: s["hourly_shade"][noon])
    ax.set_title(f"At noon, {least['name']} has the least street shade "
                 f"({least['hourly_shade'][noon]:.0%})", loc="left", fontsize=13, color=INK, pad=34)
    ax.set_ylabel("Average street shade (%)", color=INK_2)
    ax.set_ylim(0, 100)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=INK_2)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, fontsize=9)
    fig.text(0.01, 0.01, f"{built[0]['date']} · shade on an 8 m corridor around each street · "
             "buildings, OpenStreetMap trees and Meta/WRI satellite canopy", color=INK_2, fontsize=8)
    fig.tight_layout(rect=(0, 0.03, 1, 1))

    out = os.path.join("data", "compare_areas.png")
    fig.savefig(out, dpi=150)
    print(f"\nSaved {out}")
    plt.show()


if __name__ == "__main__":
    main()
