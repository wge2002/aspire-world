"""Render measured coarse geometry and timing of the offline scene smoke test."""
from __future__ import annotations
import argparse
import itertools
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
from PIL import Image


def corners(bounds):
    low, high = np.asarray(bounds)
    return np.array(list(itertools.product(*zip(low, high))))


def faces(v):
    return [v[list(ix)] for ix in [(0,1,3,2),(4,5,7,6),(0,1,5,4),
                                  (2,3,7,6),(0,2,6,4),(1,3,7,5)]]


def run(source, rgb, output):
    output.mkdir(parents=True,exist_ok=False)
    scene=json.loads((source/"scene-input.json").read_text())
    timing=json.loads((source/"perception-summary.json").read_text())
    manual=source.name=="manual-perception-control"
    title="Manual inventory / measured RGB-D geometry" if manual else "Model inventory / measured RGB-D geometry"
    entities=scene["entities"]
    known=[e for e in entities if e["measurement"]["status"]=="ok"]
    unknown=[e["id"] for e in entities if e["measurement"]["status"]!="ok"]
    colors=plt.get_cmap("tab20")(np.linspace(0,1,len(entities)))
    palette={e["id"]:colors[i] for i,e in enumerate(entities)}
    fig=plt.figure(figsize=(14,7.6),layout="constrained")
    photo=fig.add_subplot(1,2,1)
    photo.imshow(Image.open(rgb))
    photo.axis("off")
    photo.set_title("Saved initial observation / development seed 63",fontsize=12)
    ax=fig.add_subplot(1,2,2,projection="3d")
    for e in known:
        m=e["measurement"]
        v=corners(m["visible_bounds"])
        color=palette[e["id"]]
        ax.add_collection3d(Poly3DCollection(faces(v),facecolors=[color],
                                            edgecolors=[color],alpha=.16,linewidths=.9))
        p=m["position"]
        ax.scatter(*p,c=[color],s=38)
    if known:
        all_corners=np.concatenate([corners(e["measurement"]["visible_bounds"]) for e in known])
        low=np.min(all_corners,axis=0);high=np.max(all_corners,axis=0)
        pad=np.maximum((high-low)*.08,.015)
        ax.set_xlim(low[0]-pad[0],high[0]+pad[0])
        ax.set_ylim(low[1]-pad[1],high[1]+pad[1])
        ax.set_zlim(low[2]-pad[2],high[2]+pad[2])
        ax.set_box_aspect(np.maximum(high-low,.1))
    ax.set_xlabel("API reference X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_zlabel("Z (m)")
    ax.view_init(elev=28,azim=135)
    ax.set_title(f"Visible bounds: {len(known)}/{len(entities)} localized",fontsize=12)
    legend=[Patch(facecolor=palette[e["id"]],label=e["id"],alpha=.65) for e in known]
    ax.legend(handles=legend,loc="upper left",bbox_to_anchor=(.88,1),fontsize=8,frameon=False)
    fig.suptitle(title,fontsize=16)
    footer="Boxes enclose visible point quantiles, not full object shape. Dots are visible-surface medians."
    if unknown: footer+="\nUnknown: "+", ".join(unknown)
    fig.supxlabel(footer,fontsize=10)
    fig.savefig(output/"scene-overview.png",dpi=160,bbox_inches="tight",pad_inches=.25)
    plt.close(fig)

    names=["main_serial","scene_serial","scene_concurrent4"]
    labels=["Main objects\nserial","Scene objects\nserial","Scene objects\n4 client requests"]
    fig,ax=plt.subplots(figsize=(8.6,4.7),layout="constrained")
    medians=[timing[n]["median_seconds"] for n in names]
    ax.bar(range(3),medians,color=["#457b9d","#4c956c","#e9a23b"],width=.6,alpha=.8)
    for i,name in enumerate(names):
        vals=timing[name]["seconds"]
        ax.scatter([i-.08,i,i+.08],vals,c="#283342",s=28,zorder=3)
        ax.text(i,max(vals)+max(medians)*.035,f"{medians[i]:.2f}s\n{timing[name]['object_count']} objects",ha="center",fontsize=10)
    ax.set_xticks(range(3),labels)
    ax.set_ylabel("Perception batch wall time (seconds)")
    ax.set_ylim(0,max(max(timing[n]["seconds"]) for n in names)*1.28)
    ax.spines[["top","right"]].set_visible(False)
    ax.set_title("Same image / 3 repeats per condition / bars show medians",fontsize=12)
    fig.supxlabel("Includes segmentation and coarse geometry. Excludes LLM generation; GPU service serializes requests.",fontsize=9)
    fig.savefig(output/"perception-timing.png",dpi=160,bbox_inches="tight",pad_inches=.25)
    plt.close(fig)
    print(json.dumps({"output":str(output),"known":len(known),"unknown":unknown,"manual_inventory":manual}))


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--rgb",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    run(a.source,a.rgb,a.output)
