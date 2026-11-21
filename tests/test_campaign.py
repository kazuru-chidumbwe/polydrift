"""The campaign plan builds deterministically, is path-free, and keeps the version rule."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "campaign.py"


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], cwd=ROOT, capture_output=True, text=True)


def test_build_is_deterministic_and_checks_clean():
    assert run("build").returncode == 0
    first = {p.name: p.read_bytes() for p in (ROOT / "campaign" / "targets").glob("*.json")}
    assert run("build").returncode == 0
    second = {p.name: p.read_bytes() for p in (ROOT / "campaign" / "targets").glob("*.json")}
    assert first == second
    r = run("check")
    assert r.returncode == 0, r.stdout


def test_targets_have_unique_keys_and_no_host_paths():
    m = json.loads((ROOT / "campaign" / "manifest.json").read_text())
    for c in m["cells"]:
        ts = json.loads((ROOT / c["targets"]).read_text())
        keys = [t["key"] for t in ts]
        assert len(keys) == len(set(keys)), c["tag"]
        blob = json.dumps([t.get("extra", []) for t in ts])
        assert "/home/" not in blob and "/srv/" not in blob, c["tag"]


def test_clones_change_only_version_image_key():
    m = json.loads((ROOT / "campaign" / "manifest.json").read_text())
    for c in m["cells"]:
        ts = {t["key"]: t for t in json.loads((ROOT / c["targets"]).read_text())}
        for t in ts.values():
            if "cloned_from" not in t:
                continue
            src = ts[t["cloned_from"]]
            diff = {k for k in set(t) | set(src) if t.get(k) != src.get(k)}
            assert diff <= {"version", "image", "key", "cloned_from", "tag"}, (t["key"], diff)


def test_pull_job_rebuilds_recipe_images_built_with_other_arguments(tmp_path):
    r = run("jobs", "--mode", "dry", "--commit", "abc1234", "--start", "1", "--pull", "--out", str(tmp_path))
    assert r.returncode == 0, r.stderr
    pull = next(tmp_path.glob("D-001-polydrift-campaign-pull-*.sh")).read_text()
    src = json.loads((ROOT / "campaign" / "sources.json").read_text())
    sys.path.insert(0, str(ROOT / "tools"))
    import campaign
    for tag, b in src["build"].items():
        label = campaign.build_label(b)
        # present only when the label matches; the build stores the same label
        assert f"' {tag} 2>/dev/null)\" = '{label}' ]" in pull, tag
        assert f"--label 'pd.build-args={label}' -t {tag} " in pull, tag
    am26 = src["build"]["pd-apacheds:2.0.0.AM26"]["args"]
    assert am26["JDK"] == "11"


def _cases():
    import yaml
    return yaml.safe_load((ROOT / "polydrift" / "rq5" / "cases.yaml").read_text(encoding="utf-8"))["cases"]


def _vk(v):
    import re
    return [int(p) if p.isdigit() else p for p in re.split(r"[.\-]", re.sub(r"^v", "", str(v)))]


def test_recall_sets_cover_every_case_version_once():
    m = json.loads((ROOT / "campaign" / "manifest.json").read_text())
    served = {}
    for r in m["recall"]:
        ts = json.loads((ROOT / r["targets"]).read_text())
        keys = [t["key"] for t in ts]
        assert len(keys) == len(set(keys)), r["tag"]
        assert "/home/" not in json.dumps([t.get("extra", []) for t in ts]), r["tag"]
        for cid in r["cases"]:
            assert cid not in served, f"{cid} in {served.get(cid)} and {r['tag']}"
            served[cid] = r["tag"]
            c = _cases()[cid]
            for v in [*c["affected"], *(c.get("fixed") or [])]:
                hits = [t for t in ts if t["impl"] == c["family"] and str(t["version"]) == str(v)]
                assert len(hits) == 1, (r["tag"], cid, v)
    assert set(served) == set(_cases())


def test_recall_peers_are_the_campaign_current_versions():
    m = json.loads((ROOT / "campaign" / "manifest.json").read_text())
    src = {s["tag"]: s for s in json.loads((ROOT / "campaign" / "sources.json").read_text())["recall"]}
    cells = {c["tag"]: json.loads((ROOT / c["targets"]).read_text()) for c in m["cells"]}
    for r in m["recall"]:
        frm = src[r["tag"]].get("peers_from")
        if not frm:
            continue
        rec = {t["key"]: t for t in json.loads((ROOT / r["targets"]).read_text())}
        best = {}
        for t in cells[frm]:
            k = (t["impl"], t.get("role"), t["key"].replace(str(t["version"]), ""))
            if k not in best or _vk(t["version"]) > _vk(best[k]["version"]):
                best[k] = t
        for t in best.values():
            assert rec.get(t["key"]) == t, (r["tag"], t["key"])


def test_campaign_jobs_include_recall_replay_and_summary(tmp_path):
    r = run("jobs", "--mode", "campaign", "--commit", "abc1234", "--start", "1", "--out", str(tmp_path))
    assert r.returncode == 0, r.stderr
    names = sorted(p.name for p in tmp_path.iterdir())
    m = json.loads((ROOT / "campaign" / "manifest.json").read_text())
    assert sum("-rq5-" in n for n in names) == len(m["recall"])
    assert sum("-l9-" in n for n in names) == len(m["replay"])
    assert names[-1].startswith(f"D-{len(names):03d}-") and "summary" in names[-1]
    for p in tmp_path.iterdir():
        body = p.read_text()
        assert "/home/test" not in body and "abc1234" in body
        if "-rq5-" in p.name:
            assert "POLYDRIFT_FIXTURE_SET=rq5" in body and "--repeats 3" in body and "polydrift.cli recall" in body
        if "-l9-" in p.name:
            assert "--min-repeats 3" in body and "d-campcampaign-" in body
