#!/usr/bin/env python3
"""Campaign plan for the frozen polydrift run.

  build   campaign/sources.json + campaign/base/*.json -> campaign/targets/<tag>.json, campaign/manifest.json
  check   keys, versions per implementation, host paths, and (on a lab host) every image present
  pin     on a lab host: record each image's digest or local image id in campaign/pins.json
  jobs    labq scripts for the plan: --mode dry (1 repeat) or campaign (repeats from sources.json)

The targets are path-free: anything the cell mounts is written with {celldir}, so the same
files run on any host from the extracted harness tar (stability3.sh runs from its root).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAMP = ROOT / "campaign"


def load_list(p: Path) -> list[dict]:
    d = json.loads(p.read_text())
    return d if isinstance(d, list) else d.get("targets", [])


def retag(image: str, old: str, new: str) -> str:
    """Swap the version in an image tag, keeping any tag prefix (memory-3.9.1, v1.1.0)."""
    if "@" in image:
        raise ValueError(f"cannot clone a digest-pinned image without an explicit image: {image}")
    repo, tag = image.rsplit(":", 1)
    if old not in tag:
        raise ValueError(f"version {old} not in tag {image}")
    return f"{repo}:{tag.replace(old, new)}"


def build_cell(spec: dict) -> list[dict]:
    seen: dict[str, dict] = {}
    for name in spec["base"]:
        for e in load_list(CAMP / "base" / name):
            if isinstance(e, dict) and e.get("key") and e["key"] not in seen:
                seen[e["key"]] = json.loads(json.dumps(e))
    out = list(seen.values())
    for impl, rule in spec.get("clone", {}).items():
        src = [e for e in out if e.get("impl") == impl and e.get("version") == rule["from"]]
        if not src:
            raise SystemExit(f"{spec['tag']}: no {impl} {rule['from']} entry to clone from")
        for to in rule["to"]:
            ver, image = (to if isinstance(to, list) else (to, None))
            for e in src:
                c = json.loads(json.dumps(e))
                c["version"] = ver
                c["image"] = image or retag(e["image"], rule["from"], ver)
                c["key"] = e["key"].replace(rule["from"], ver)
                c["cloned_from"] = e["key"]
                c.pop("tag", None)  # the source's upstream tag does not describe the clone
                if c["key"] not in seen:
                    seen[c["key"]] = c
                    out.append(c)
    rw = spec.get("rewrite", {})
    if rw:
        for e in out:
            e["extra"] = [_rewrite(x, rw) for x in e.get("extra", [])]
    return out


def _rewrite(x, rw):
    if isinstance(x, str):
        for a, b in rw.items():
            x = x.replace(a, b)
    return x


def ver_key(v: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"[.\-]", re.sub(r"^v", "", v))]


def cmd_build(_a) -> int:
    src = json.loads((CAMP / "sources.json").read_text())
    (CAMP / "targets").mkdir(exist_ok=True)
    manifest = {"repeats": src["repeats"], "cells": [], "exceptions": src.get("exceptions", {})}
    for spec in src["cells"]:
        ts = build_cell(spec)
        ts.sort(key=lambda e: (e.get("impl", ""), e.get("role", ""), ver_key(str(e.get("version", "")))))
        rel = f"campaign/targets/{spec['tag']}.json"
        (ROOT / rel).write_text(json.dumps(ts, indent=1) + "\n")
        versions = defaultdict(set)
        for e in ts:
            versions[e.get("impl")].add(str(e.get("version")))
        manifest["cells"].append({
            "tag": spec["tag"], "cell": spec["cell"], "fixtures": spec["fixtures"], "lab": spec["lab"],
            "targets": rel, "n_targets": len(ts),
            "versions": {k: sorted(v, key=ver_key, reverse=True) for k, v in sorted(versions.items())},
        })
        print(f"{spec['tag']:15s} {len(ts):3d} targets  " + "  ".join(f"{k}:{len(v)}" for k, v in sorted(versions.items())))
    (CAMP / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return 0


LABEL = "pd.build-args"


def build_label(b: dict) -> str:
    """The build arguments of a recipe image as one string, stored on the image as a label."""
    return ";".join(f"{k}={v}" for k, v in sorted(b.get("args", {}).items()))


def host_images() -> tuple[set[str], callable]:
    tags = set(subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                              capture_output=True, text=True, check=True).stdout.split())

    def inspect(ref: str) -> dict | None:
        r = subprocess.run(["docker", "image", "inspect", ref], capture_output=True, text=True)
        return json.loads(r.stdout)[0] if r.returncode == 0 else None
    return tags, inspect


def mount_sources(extra: list) -> list[str]:
    out = []
    for i, x in enumerate(extra):
        if x in ("-v", "--volume", "--mount") and i + 1 < len(extra):
            v = str(extra[i + 1])
            out.append(v.split(":", 1)[0] if x != "--mount" else v)
    return out


def cmd_check(a) -> int:
    m = json.loads((CAMP / "manifest.json").read_text())
    exc = m.get("exceptions", {})
    problems, notes = [], []
    tags, inspect = host_images() if a.images else (set(), None)
    builds = json.loads((CAMP / "sources.json").read_text()).get("build", {})
    for c in m["cells"]:
        ts = load_list(ROOT / c["targets"])
        keys = [e.get("key") for e in ts]
        dup = {k for k in keys if keys.count(k) > 1}
        if dup:
            problems.append(f"{c['tag']}: duplicate keys {sorted(dup)}")
        for impl, vs in c["versions"].items():
            if len(vs) < 3:
                (notes if impl in exc else problems).append(
                    f"{c['tag']}: {impl} has {len(vs)} version(s) {vs}" + (f" [exception: {exc[impl]}]" if impl in exc else ""))
        for e in ts:
            for s in mount_sources(e.get("extra", [])):
                if s.startswith("/") or "/home/" in s or "/srv/" in s:
                    problems.append(f"{c['tag']}: {e['key']} mounts a host path {s}")
            if a.images:
                img = e["image"]
                info = inspect(img)
                if not (img in tags or info):
                    problems.append(f"{c['tag']}: {e['key']} image missing on host: {img}")
                elif img in builds and info is not None:
                    have = ((info.get("Config") or {}).get("Labels") or {}).get(LABEL)
                    if have != build_label(builds[img]):
                        problems.append(f"{c['tag']}: {e['key']} image {img} was not built with the arguments in "
                                        f"campaign/sources.json (label {have!r})")
    for n in notes:
        print("NOTE", n)
    for p in problems:
        print("FAIL", p)
    print(f"check: {len(problems)} problem(s), {len(notes)} noted exception(s)")
    return 1 if problems else 0


def cmd_pin(_a) -> int:
    m = json.loads((CAMP / "manifest.json").read_text())
    _, inspect = host_images()
    pins = {}
    for c in m["cells"]:
        for e in load_list(ROOT / c["targets"]):
            info = inspect(e["image"])
            if info is None:
                pins[e["image"]] = None
                continue
            pins[e["image"]] = {"id": info["Id"], "repo_digests": info.get("RepoDigests", [])}
    (CAMP / "pins.json").write_text(json.dumps(pins, indent=1, sort_keys=True) + "\n")
    missing = [k for k, v in pins.items() if v is None]
    print(f"pinned {len(pins) - len(missing)} image(s); missing {len(missing)}: {missing}")
    return 1 if missing else 0


JOB_HEAD = """#!/bin/bash
# polydrift {what} on Testbed D at harness {c}: cell {cell} ({tag}), every version in campaign/targets/{tag}.json,
# {rep} repeat(s). Written by tools/campaign.py jobs. Flag ~/polydrift/stability/{flagtag}-DONE
set -u
export REPEATS={rep}
TAR=~/polydrift/harness-{c}.tar
FLAG=~/polydrift/stability/{flagtag}-DONE
mkdir -p ~/polydrift/stability
[ -f $TAR ] || {{ echo "NO-HARNESS-TAR $TAR" > $FLAG; exit 1; }}
tar -xOf $TAR campaign/targets/{tag}.json > /tmp/pd-{flagtag}.json || {{ echo "NO-TARGETS" > $FLAG; exit 1; }}
miss=$(python3 -c "import json,subprocess,sys
for x in json.load(open(sys.argv[1])):
    if subprocess.run(['docker','image','inspect',x['image']],capture_output=True).returncode: print(x['image'])" /tmp/pd-{flagtag}.json | sort -u | tr '\\n' ' ')
if [ -n "$miss" ]; then echo "MISSING-IMAGES $miss" > $FLAG; exit 1; fi
bash ~/labtools/stability3.sh {cell} campaign/targets/{tag}.json $TAR {c} {fixtures} {flagtag}{lab}
"""

PULL = """#!/bin/bash
# Images for the campaign plan (harness {c}): first build the recipe images listed under "build" in
# campaign/sources.json, then pull every public image that is missing.
# Locally built pd-* images are never pulled; a missing one is listed. Idempotent.
# Flag ~/polydrift/campaign-{c}-PULL-DONE (lists what was built, pulled or failed).
set -u
TAR=~/polydrift/harness-{c}.tar
F=~/polydrift/campaign-{c}-PULL-DONE; : > $F.tmp
PX="--network host"   # Testbed D has direct internet since 18 Nov 2026 (no proxy)
BH=~/polydrift/campaign-{c}-build; rm -rf $BH; mkdir -p $BH; tar -xf $TAR -C $BH
{builds}
for t in $(tar -tf $TAR | grep '^campaign/targets/.*\\.json$'); do tar -xOf $TAR $t; done > /tmp/pd-campaign-all.json.txt
python3 - <<'P' > /tmp/pd-campaign-images.txt
import json,re
txt=open('/tmp/pd-campaign-all.json.txt').read()
dec=json.JSONDecoder(); i=0; imgs=set()
while i < len(txt):
    while i < len(txt) and txt[i].isspace(): i+=1
    if i>=len(txt): break
    obj,i=dec.raw_decode(txt,i)
    for e in obj: imgs.add(e['image'])
print('\\n'.join(sorted(imgs)))
P
while read -r img; do
  docker image inspect "$img" >/dev/null 2>&1 && continue
  case "$img" in pd-*) echo "LOCAL-MISSING $img" >> $F.tmp; continue;; esac
  docker pull -q "$img" >/dev/null 2>&1 && echo "PULLED $img" >> $F.tmp || echo "PULL-FAILED $img" >> $F.tmp
done < /tmp/pd-campaign-images.txt
mv $F.tmp $F
"""

SUMMARY = """#!/bin/bash
# Summary of the {what} jobs at {c}. Flag ~/polydrift/{flag}
O=~/polydrift/{summary}; : > $O
for f in ~/polydrift/stability/{prefix}-*-DONE; do
  echo "== $(basename $f): $(head -c 300 $f)" >> $O
  d=$(head -1 $f); [ -f "$d/STABILITY.txt" ] && head -25 "$d/STABILITY.txt" >> $O
  [ -d "$d" ] && python3 ~/labtools/stabdiff.py "$d" >> $O 2>&1
done
df -h /srv/lab | tail -1 >> $O
touch ~/polydrift/{flag}
"""


def cmd_jobs(a) -> int:
    m = json.loads((CAMP / "manifest.json").read_text())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rep = 1 if a.mode == "dry" else m["repeats"]
    what = "campaign dry run" if a.mode == "dry" else "CAMPAIGN"
    prefix = f"d-camp{a.mode}"
    n = a.start
    written = []

    def w(name: str, body: str):
        p = out / name
        p.write_bytes(body.encode())
        written.append(p.name)

    if a.pull:
        src = json.loads((CAMP / "sources.json").read_text())
        lines = []
        for tag, b in src.get("build", {}).items():
            args = " ".join(f"--build-arg {k}={v}" for k, v in b.get("args", {}).items())
            label = build_label(b)
            log = "$BH/build-" + re.sub(r"[:/]", "_", tag) + ".log"
            # An image that is present but was built with other arguments (label differs) is rebuilt.
            lines.append(
                f'if [ "$(docker image inspect -f \'{{{{index .Config.Labels "{LABEL}"}}}}\' {tag} 2>/dev/null)" = \'{label}\' ]; '
                f'then echo "PRESENT {tag}" >> $F.tmp; '
                f'elif nice -n 5 docker build $PX -f $BH/{b["dockerfile"]} {args} --label \'{LABEL}={label}\' -t {tag} '
                f'$BH/{b["context"]} > {log} 2>&1; '
                f'then echo "BUILT {tag} $(grep -m1 -o "TARBALL-SHA256 [0-9a-f]*" {log})" >> $F.tmp; '
                f'else echo "BUILD-FAILED {tag} (log {log})" >> $F.tmp; fi')
        w(f"D-{n:03d}-polydrift-campaign-pull-{a.commit}.sh", PULL.format(c=a.commit, builds="\n".join(lines)))
        n += 1
    for c in m["cells"]:
        w(f"D-{n:03d}-polydrift-{prefix}-{c['tag']}-{a.commit}.sh", JOB_HEAD.format(
            what=what, c=a.commit, cell=c["cell"], tag=c["tag"], rep=rep, flagtag=f"{prefix}-{c['tag']}",
            fixtures=c["fixtures"], lab=" lab" if c["lab"] else ""))
        n += 1
    w(f"D-{n:03d}-polydrift-{prefix}-summary-{a.commit}.sh", SUMMARY.format(
        what=what, c=a.commit, prefix=prefix, summary=f"D-CAMP-{a.mode.upper()}-SUMMARY.txt",
        flag=f"D-CAMP-{a.mode.upper()}-DONE"))
    print("\n".join(written))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    c = sub.add_parser("check")
    c.add_argument("--images", action="store_true", help="also require every image on this host")
    sub.add_parser("pin")
    j = sub.add_parser("jobs")
    j.add_argument("--mode", choices=["dry", "campaign"], required=True)
    j.add_argument("--commit", required=True)
    j.add_argument("--start", type=int, required=True, help="first D-NNN job number")
    j.add_argument("--out", required=True)
    j.add_argument("--pull", action="store_true", help="first job pulls missing public images")
    a = ap.parse_args()
    return {"build": cmd_build, "check": cmd_check, "pin": cmd_pin, "jobs": cmd_jobs}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
