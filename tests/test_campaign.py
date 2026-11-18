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
            assert diff <= {"version", "image", "key", "cloned_from"}, (t["key"], diff)
