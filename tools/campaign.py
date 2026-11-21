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
        (ROOT / rel).write_text(json.dumps(ts, indent=1) + "\n", newline="\n")
        versions = defaultdict(set)
        for e in ts:
            versions[e.get("impl")].add(str(e.get("version")))
        manifest["cells"].append({
            "tag": spec["tag"], "cell": spec["cell"], "fixtures": spec["fixtures"], "lab": spec["lab"],
            "targets": rel, "n_targets": len(ts),
            "versions": {k: sorted(v, key=ver_key, reverse=True) for k, v in sorted(versions.items())},
        })
        print(f"{spec['tag']:15s} {len(ts):3d} targets  " + "  ".join(f"{k}:{len(v)}" for k, v in sorted(versions.items())))
    cell_targets = {c["tag"]: load_list(ROOT / c["targets"]) for c in manifest["cells"]}
    manifest["recall"] = []
    for spec in src.get("recall", []):
        ts = build_recall(spec, cell_targets)
        rel = f"campaign/targets/{spec['tag']}.json"
        (ROOT / rel).write_text(json.dumps(ts, indent=1) + "\n", newline="\n")
        manifest["recall"].append({"tag": spec["tag"], "cell": spec["cell"], "lab": spec["lab"], "targets": rel,
                                   "cases": spec_cases(spec), "n_targets": len(ts), "keys": [e["key"] for e in ts]})
        print(f"{spec['tag']:15s} {len(ts):3d} targets  " + " ".join(e["key"] for e in ts))
    manifest["replay"] = src.get("replay", [])
    (CAMP / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", newline="\n")
    return 0


def load_rq5_cases() -> dict:
    import yaml
    return (yaml.safe_load((ROOT / "polydrift" / "rq5" / "cases.yaml").read_text(encoding="utf-8")) or {})["cases"]


def spec_cases(spec: dict) -> list[str]:
    """The RQ5 cases a recall target set serves: those of its cell whose family the set names."""
    cases = load_rq5_cases()
    return sorted(k for k, c in cases.items() if c["cell"] == spec["cell"] and c["family"] in spec["families"])


def build_recall(spec: dict, cell_targets: dict[str, list[dict]]) -> list[dict]:
    """RQ5 recall targets: the affected and fixed versions of every case the set serves (validated entries from
    campaign/base/rq5/), plus the current peers. With peers_from, the peers are the highest version of every
    (implementation, role, variant) in that campaign cell, so recall compares against exactly the campaign's current
    versions; without it (a role no campaign cell holds), the base file's own current entries are the peers."""
    cases = load_rq5_cases()
    want = defaultdict(set)
    for cid in spec_cases(spec):
        c = cases[cid]
        for v in [*c.get("affected", []), *(c.get("fixed") or [])]:
            want[c["family"]].add(str(v))
    base: list[dict] = []
    for name in spec["base"]:
        base += [json.loads(json.dumps(e)) for e in load_list(CAMP / "base" / name)]
    out: dict[str, dict] = {}
    if spec.get("peers_from"):
        best: dict[tuple, dict] = {}
        for e in cell_targets[spec["peers_from"]]:
            k = (e.get("impl"), e.get("role"), e["key"].replace(str(e.get("version")), ""))
            if k not in best or ver_key(str(e["version"])) > ver_key(str(best[k]["version"])):
                best[k] = e
        for e in best.values():
            out[e["key"]] = json.loads(json.dumps(e))
        for e in base:
            if str(e.get("version")) in want.get(e.get("impl"), set()):
                out.setdefault(e["key"], e)
        # Affected or fixed versions with no validated entry of their own: a clone of the campaign's entry for that
        # implementation that changes only version, image and key (as the cells' clone rule).
        for impl, rule in spec.get("clone", {}).items():
            src = [e for e in cell_targets[spec["peers_from"]] if e.get("impl") == impl and str(e.get("version")) == rule["from"]]
            if not src:
                raise SystemExit(f"{spec['tag']}: no {impl} {rule['from']} entry to clone from")
            for to in rule["to"]:
                ver, image = (to if isinstance(to, list) else (to, None))
                for e in src:
                    c = json.loads(json.dumps(e))
                    c["version"], c["image"] = ver, image or retag(e["image"], rule["from"], ver)
                    c["key"], c["cloned_from"] = e["key"].replace(rule["from"], ver), e["key"]
                    c.pop("tag", None)
                    out.setdefault(c["key"], c)
    else:
        for e in base:
            out.setdefault(e["key"], e)
    rw = spec.get("rewrite", {})
    if rw:
        for e in out.values():
            e["extra"] = [_rewrite(x, rw) for x in e.get("extra", [])]
    missing = [f"{f} {v}" for f, vs in sorted(want.items()) for v in sorted(vs)
               if not any(e.get("impl") == f and str(e.get("version")) == v for e in out.values())]
    if missing:
        raise SystemExit(f"{spec['tag']}: no target for case version(s) {missing}")
    return sorted(out.values(), key=lambda e: (e.get("impl", ""), e.get("role") or "", ver_key(str(e.get("version", "")))))


LABEL = "pd.build-args"


def recipe_hash(b: dict) -> str:
    """sha256 over the Dockerfile and every file in the build context (path and content, CRLF read as LF), so an
    image built from an older recipe never passes as current, also when the recipe takes no build arguments."""
    import hashlib
    h = hashlib.sha256()
    files = {ROOT / b["dockerfile"]} | {p for p in (ROOT / b["context"]).rglob("*")
                                        if p.is_file() and "__pycache__" not in p.parts}
    for p in sorted(files):
        h.update(p.relative_to(ROOT).as_posix().encode() + b"\0" + p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:16]


def build_label(b: dict) -> str:
    """The build arguments and recipe hash of a recipe image as one string, stored on the image as a label."""
    return ";".join([*(f"{k}={v}" for k, v in sorted(b.get("args", {}).items())), f"recipe={recipe_hash(b)}"])


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
    cases = load_rq5_cases()
    served = set()
    for r in m.get("recall", []):
        served |= set(r["cases"])
        for e in load_list(ROOT / r["targets"]):
            for s in mount_sources(e.get("extra", [])):
                if s.startswith("/") or "/home/" in s or "/srv/" in s:
                    problems.append(f"{r['tag']}: {e['key']} mounts a host path {s}")
            if a.images and not (e["image"] in tags or inspect(e["image"])):
                problems.append(f"{r['tag']}: {e['key']} image missing on host: {e['image']}")
    for cid in sorted(set(cases) - served):
        problems.append(f"RQ5 case {cid} is served by no recall target set")
    for rp in m.get("replay", []):
        if rp["from"] not in {c["tag"] for c in m["cells"]}:
            problems.append(f"replay {rp['tag']}: no campaign cell {rp['from']}")
    if a.images:
        for img, b in builds.items():
            info = inspect(img)
            if info is None:
                problems.append(f"recipe image missing on host: {img}")
            elif ((info.get("Config") or {}).get("Labels") or {}).get(LABEL) != build_label(b):
                problems.append(f"recipe image {img} was not built with the arguments in campaign/sources.json")
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
    (CAMP / "pins.json").write_text(json.dumps(pins, indent=1, sort_keys=True) + "\n", newline="\n")
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

RECALL_JOB = """#!/bin/bash
# polydrift {what}, RQ5 recall {tag} at harness {c}: every known-bug fixture (cells/{cell}/fixtures_rq5.yaml) on the
# affected and fixed versions of cases {cases} and the campaign's current peers (campaign/targets/{tag}.json),
# {rep} repeat(s); then the recall outcome per case (polydrift recall). Written by tools/campaign.py jobs.
# Flag ~/polydrift/stability/{flagtag}-DONE (first line: the run directory).
set -u
C={c}; TAR=~/polydrift/harness-$C.tar; FLAG=~/polydrift/stability/{flagtag}-DONE
mkdir -p ~/polydrift/stability ~/polydrift/recall
[ -f $TAR ] || {{ echo "NO-HARNESS-TAR $TAR" > $FLAG; exit 1; }}
H=~/polydrift/recall/harness-$C-{tag}; rm -rf $H; mkdir -p $H; tar xf $TAR -C $H; echo $C > $H/COMMIT
miss=$(python3 -c "import json,subprocess,sys
for x in json.load(open(sys.argv[1])):
    if subprocess.run(['docker','image','inspect',x['image']],capture_output=True).returncode: print(x['image'])" $H/campaign/targets/{tag}.json | sort -u | tr '\\n' ' ')
if [ -n "$miss" ]; then echo "MISSING-IMAGES $miss" > $FLAG; exit 1; fi
cd $H; export POLYDRIFT_COMMIT=$C POLYDRIFT_FIXTURE_SET=rq5
O=~/polydrift/recall/{flagtag}-r{rep}-$(date -u +%Y%m%dT%H%M%SZ)
python3 -m polydrift.cells.{cell}.runner --targets campaign/targets/{tag}.json{lab} --out $O --repeats {rep} > $O.out 2>&1
python3 ~/labtools/labsum.py $O > $O/SMOKE.txt 2>&1; cat $O/progress >> $O/SMOKE.txt 2>/dev/null
python3 -m polydrift.cli recall $O/records.jsonl --cases {cases} --min-repeats {rep} --out $O/RECALL.json > $O/RECALL.txt 2>&1
echo "$O" > $FLAG
"""

REPLAY_JOB = """#!/bin/bash
# polydrift {what}, L9 consumer replay {tag} at harness {c}: replays the sampled pairs of the campaign run of cell
# {src} (its flag ~/polydrift/stability/{srcflag}-DONE names the run) through the consumers of cells/{cell}/lab.
# Written by tools/campaign.py jobs. Flag ~/polydrift/stability/{flagtag}-DONE (first line: the replay directory).
set -u
C={c}; TAR=~/polydrift/harness-$C.tar; FLAG=~/polydrift/stability/{flagtag}-DONE
[ -f $TAR ] || {{ echo "NO-HARNESS-TAR $TAR" > $FLAG; exit 1; }}
R=$(head -1 ~/polydrift/stability/{srcflag}-DONE 2>/dev/null)
[ -n "$R" ] && [ -f "$R/raw.jsonl" ] || {{ echo "NO-CELL-RUN {srcflag} ($R)" > $FLAG; exit 1; }}
for img in {images}; do docker image inspect $img > /dev/null 2>&1 || {{ echo "MISSING-IMAGE $img" > $FLAG; exit 1; }}; done
H=~/polydrift/replay/harness-$C-{tag}; rm -rf $H; mkdir -p $H; tar xf $TAR -C $H; echo $C > $H/COMMIT
cd $H; export POLYDRIFT_COMMIT=$C
O=$R-replay; rm -rf $O
python3 -m polydrift.cells.{cell}.replay --run $R{targets} --lab $H/polydrift/cells/{cell}/lab --out $O --min-repeats {rep} > $O.out 2>&1
echo "$O" > $FLAG
"""

PARDIFF_JOB = """#!/bin/bash
# polydrift {what}, RQ2 static-analysis contrast at harness {c}: ParDiff (b0a3098, release build) on the MQTT pair,
# Run A as published and Run B with the skip-list patch (campaign/pardiff). Each run capped at 6 h and 4 GiB of output.
# Written by tools/campaign.py jobs. Flag ~/polydrift/stability/{flagtag}-DONE (first line: the result directory).
set -u
C={c}; TAR=~/polydrift/harness-$C.tar; FLAG=~/polydrift/stability/{flagtag}-DONE
[ -f $TAR ] || {{ echo "NO-HARNESS-TAR $TAR" > $FLAG; exit 1; }}
H=~/polydrift/pardiff/harness-$C; rm -rf $H; mkdir -p $H; tar xf $TAR -C $H; echo $C > $H/COMMIT
O=~/polydrift/pardiff/{flagtag}-$C
bash $H/campaign/pardiff/run_mqtt_ab.sh $H $O > $O.out 2>&1
echo "$O/mqtt" > $FLAG
"""

SUMMARY = """#!/bin/bash
# Summary of the {what} jobs at {c}. Flag ~/polydrift/{flag}
O=~/polydrift/{summary}; : > $O
for f in ~/polydrift/stability/{prefix}-*-DONE; do
  echo "== $(basename $f): $(head -c 300 $f)" >> $O
  d=$(head -1 $f); [ -f "$d/STABILITY.txt" ] && head -25 "$d/STABILITY.txt" >> $O
  [ -f "$d/RECALL.txt" ] && cat "$d/RECALL.txt" >> $O
  [ -f "$d/summary.json" ] && head -c 3000 "$d/summary.json" >> $O && echo >> $O
  [ -f "$d/progress" ] && tail -3 "$d/progress" >> $O
  [ -f "$d/SUMMARY.txt" ] && cat "$d/SUMMARY.txt" >> $O
  case "$f" in *-rq5-*|*-l9-*|*-pardiff-*) ;; *) [ -d "$d" ] && python3 ~/labtools/stabdiff.py "$d" >> $O 2>&1;; esac
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
            # An image that is present but was built with other arguments (label differs) is rebuilt; a missing
            # image is always built (inspect prints nothing for it, which must not pass for an empty label).
            lines.append(
                f'if docker image inspect {tag} >/dev/null 2>&1 && [ "$(docker image inspect -f \'{{{{index .Config.Labels "{LABEL}"}}}}\' {tag} 2>/dev/null)" = \'{label}\' ]; '
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
    for r in m.get("recall", []):
        w(f"D-{n:03d}-polydrift-{prefix}-{r['tag']}-{a.commit}.sh", RECALL_JOB.format(
            what=what, c=a.commit, tag=r["tag"], cell=r["cell"], cases=",".join(r["cases"]), rep=rep,
            flagtag=f"{prefix}-{r['tag']}", lab=f" --lab $H/polydrift/cells/{r['cell']}/lab" if r["lab"] else ""))
        n += 1
    for rp in m.get("replay", []):
        w(f"D-{n:03d}-polydrift-{prefix}-l9-{rp['tag']}-{a.commit}.sh", REPLAY_JOB.format(
            what=what, c=a.commit, tag=rp["tag"], cell=rp["cell"], src=rp["from"], srcflag=f"{prefix}-{rp['from']}",
            flagtag=f"{prefix}-l9-{rp['tag']}", images=" ".join(rp["images"]), rep=rep,
            targets=f" --targets $H/campaign/targets/{rp['from']}.json" if rp.get("targets") else ""))
        n += 1
    w(f"D-{n:03d}-polydrift-{prefix}-pardiff-mqtt-{a.commit}.sh", PARDIFF_JOB.format(
        what=what, c=a.commit, flagtag=f"{prefix}-pardiff-mqtt"))
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
