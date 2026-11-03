"""Render the system's logical flow in a compact horizontal report layout."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.path import Path as PlotPath

ROOT = Path(__file__).resolve().parent
fig, ax = plt.subplots(figsize=(12, 5.6))
ax.set_xlim(0, 122)
ax.set_ylim(0, 57)
ax.axis("off")

def box(x, y, width, height, label, color="#ffffff", size=12, edge="#8091a0"):
    ax.add_patch(FancyBboxPatch((x, y), width, height, boxstyle="round,pad=0.2,rounding_size=0.6",
                               facecolor=color, edgecolor=edge, linewidth=1, zorder=2))
    lines = label.split("\n")
    step = size * 0.17
    for index, line in enumerate(lines):
        ax.text(x+width/2, y+height/2 + ((len(lines)-1)/2-index)*step,
                line, ha="center", va="center", fontsize=size,
                weight="bold" if index == 0 else "normal",
                family="DejaVu Sans", color="#172734", zorder=4)

def arrow(points, dashed=False, label=None, at=None):
    path = PlotPath(points, [PlotPath.MOVETO] + [PlotPath.LINETO]*(len(points)-1))
    ax.add_patch(FancyArrowPatch(path=path, arrowstyle="-|>", mutation_scale=12,
                                 linewidth=1.1, color="#586a78", linestyle="--" if dashed else "-", zorder=3))
    if label:
        ax.text(*at, label, ha="center", va="center", fontsize=9.5, color="#405462",
                bbox={"facecolor":"white", "edgecolor":"none", "pad":0.6}, zorder=5)

box(20, 1, 77, 54, "", "#f3f6fa")
ax.text(58.5, 52.8, "Server · one asyncio event loop", ha="center", fontsize=13, weight="bold", color="#294253")
box(22, 3, 72, 24, "", "#edf4ed", edge="#98ad99")
ax.text(58, 25.2, "Per-client sessions · cleanup + monitoring", ha="center", fontsize=10.5, color="#355339")

box(0.7, 23, 15.8, 17, "Caption source\nfull-text\nrevisions", "#e7eef5")
box(24, 41, 25, 8, "CaptionState\nlatest provisional per segment\nfinalized IDs / revisions", size=10.5)
box(56, 41, 24, 8, "History\nfull final captions\n90 s / 5,000 items", size=11)
box(24, 30, 25, 8, "Broadcast + policy\nqueue/send pressure", size=11)
box(56, 30, 24, 8, "Client handler\nhistory + active state", size=11)
box(27, 16.5, 24, 7, "Queue 1\n256 msg / 1 MiB", size=11)
box(62, 16.5, 26, 7, "Sender task 1\nasync socket writes", size=11)
box(27, 6.5, 24, 7, "Queue N\n256 msg / 1 MiB", size=11)
box(62, 6.5, 26, 7, "Sender task N\nasync socket writes", size=11)

box(101.5, 5.5, 20, 40, "", "#faf5ec", edge="#b1a08a")
ax.text(111.5, 43.4, "Each browser", ha="center", fontsize=12, weight="bold", color="#5b4a31")
box(103, 33.5, 17, 7, "Revision checks", size=10.5)
box(103, 21.5, 17, 7, "Live view", size=11)
box(103, 9, 17, 8, "Local final recall\n15 / 30 / 60 s", size=10.5)

arrow([(16.7, 34), (21.8, 34), (21.8, 45), (23.7, 45)])
ax.text(8.5, 42, "WebSocket", ha="center", fontsize=10, color="#405462")
arrow([(49.2, 45), (55.7, 45)], label="finals", at=(52.5, 46.5))
arrow([(36.5, 40.8), (36.5, 38.3)], label="accepted", at=(42, 39.6))
arrow([(68, 40.8), (68, 38.3)], dashed=True)
arrow([(49.2, 43), (52.5, 43), (52.5, 34), (55.7, 34)], dashed=True)
arrow([(36.5, 29.8), (36.5, 28.5), (24, 28.5), (24, 20), (26.7, 20)])
arrow([(68, 29.8), (68, 27.3)], dashed=True, label="initial batch", at=(79.5, 28.6))
arrow([(24, 20), (24, 10), (26.7, 10)])
arrow([(51.2, 20), (61.7, 20)])
arrow([(51.2, 10), (61.7, 10)])
arrow([(88.2, 20), (98.6, 20), (98.6, 38.5), (102.8, 38.5)])
arrow([(88.2, 10), (100.1, 10), (100.1, 35.5), (102.8, 35.5)])
arrow([(111.5, 33.3), (111.5, 28.8)])
arrow([(111.5, 21.3), (111.5, 17.3)], label="finals", at=(115.5, 19.3))
arrow([(111.5, 45.7), (111.5, 50.3), (83.5, 50.3), (83.5, 34), (80.3, 34)], dashed=True)
ax.text(98, 51.4, "HELLO + final cursor", ha="center", fontsize=9.5, color="#405462")
fig.subplots_adjust(left=0.005, right=0.995, top=0.99, bottom=0.01)
fig.savefig(ROOT / "system_architecture.png", dpi=230, facecolor="white", bbox_inches="tight")
plt.close(fig)
print("Rendered compact horizontal architecture.")
