#!/usr/bin/env python3
"""Sieve cell engine wrapper (manuscript 4.5). Mounted read-only at /lab in every engine image and run once per
fixture in a fresh container:

  python3 /lab/run.py --engine ENGINE --fx /fx

/fx holds script.sieve, msg.eml and meta.json {"from": ..., "to": ..., "folders": ["INBOX", "INBOX.Quarantine"]}.
It prints one JSON object with three layers, using only the engine's own tools:

  compile  "ok" | "refused", and compile_class: unknown-extension | syntax | other (from the engine's message)
  intent   the engine's own action list (test or dry-run mode), normalised: keep, fileinto:F, redirect:A, reject,
           discard, vacation, notify, flags:..., error
  effect   after a real execution against a lab mailbox, where available: message counts per folder and the
           messages the engine sent out (kept by capture.sh), each classified from its headers
           (redirect | reject | vacation | dsn | other), with its recipients

Engines: pigeonhole (sieve-test), mailutils (sieve), exim (exim -bf, intent only), sieve-rs (pd-sieve-rs runner,
intent only), jsieve (pd-jsieve runner, intent only), cyrus (sievec compile; effect over LMTP, phase 2).
"""

from __future__ import annotations

import argparse
import email
import json
import os
import re
import shutil
import subprocess
from email import policy
from pathlib import Path

OUT = Path("/tmp/pd-out")


def sh(args: list[str], stdin: bytes | None = None, timeout: int = 60, env: dict | None = None) -> tuple[int, str]:
    r = subprocess.run(args, input=stdin, capture_output=True, timeout=timeout, env={**os.environ, **(env or {})})
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def compile_class(text: str) -> str:
    t = text.lower()
    if re.search(r"unknown (extension|capability)|unsupported (extension|capability)|not (supported|available)|"
                 r"require.*(unknown|unsupported|not supported)|extension .* is not", t):
        return "unknown-extension"
    if re.search(r"syntax|parse|unexpected|expected|missing|invalid", t):
        return "syntax"
    return "other"


def emitted() -> list[dict]:
    out = []
    for f in sorted(OUT.glob("*.eml"), key=lambda p: int(p.stem)):
        args = (OUT / f"{f.stem}.args").read_text(errors="replace").split("\n") if (OUT / f"{f.stem}.args").exists() else []
        m = email.message_from_bytes(f.read_bytes(), policy=policy.compat32)
        ctype = (m.get("Content-Type") or "").lower()
        auto = (m.get("Auto-Submitted") or "").lower()
        if "disposition-notification" in ctype or "report-type=disposition" in ctype:
            kind = "reject"
        elif "delivery-status" in ctype:
            kind = "dsn"
        elif auto.startswith("auto-replied") or m.get("X-Sieve-Vacation"):
            kind = "vacation"
        elif m.get("Message-ID") and m.get("Subject") is not None and not auto:
            kind = "redirect"
        else:
            kind = "other"
        rcpt = sorted(a for a in args if "@" in a and not a.startswith("-"))
        out.append({"kind": kind, "to": rcpt, "auto_submitted": auto or None})
    return out


def maildir_counts(root: Path) -> dict[str, int]:
    counts = {}
    for d in [root, *sorted(p for p in root.glob(".*") if p.is_dir() and p.name not in (".", ".."))]:
        if not (d / "cur").exists() and not (d / "new").exists():
            continue
        name = "INBOX" if d == root else "INBOX" + d.name
        counts[name] = sum(1 for sub in ("cur", "new") for x in (d / sub).glob("*") if x.is_file())
    return counts


def make_maildir(root: Path, folders: list[str]) -> None:
    for f in folders:
        d = root if f == "INBOX" else root / ("." + f.split(".", 1)[1] if f.startswith("INBOX.") else "." + f)
        for sub in ("cur", "new", "tmp"):
            (d / sub).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- Pigeonhole (Dovecot)
PH_ACTION = [
    (r"store message in folder:\s*(\S+)", lambda m: f"fileinto:{m.group(1)}"),
    (r"redirect message to:\s*(\S+)", lambda m: f"redirect:{m.group(1)}"),
    (r"reject message", lambda m: "reject"),
    (r"discard", lambda m: "discard"),
    (r"send vacation", lambda m: "vacation"),
    (r"send notification", lambda m: "notify"),
]


def ph_conf(ver: str, maildir: Path) -> Path:
    c = Path("/tmp/pd-dovecot.conf")
    if ver.startswith("2.4"):
        c.write_text(f"""dovecot_config_version = 2.4.0
dovecot_storage_version = 2.4.0
mail_driver = maildir
mail_path = {maildir}
mail_inbox_path = {maildir}
mail_home = /tmp/pd-home
postmaster_address = postmaster@lab.test
sendmail_path = /lab/capture.sh
""")
    else:
        c.write_text(f"""mail_location = maildir:{maildir}
mail_home = /tmp/pd-home
postmaster_address = postmaster@lab.test
sendmail_path = /lab/capture.sh
""")
    return c


def pigeonhole(fx: Path, meta: dict) -> dict:
    ver = sh(["dovecot", "--version"])[1].split()[0]
    s, m = Path("/tmp/s.sieve"), Path("/tmp/m.eml")
    shutil.copy(fx / "script.sieve", s)
    shutil.copy(fx / "msg.eml", m)
    rc, txt = sh(["sievec", str(s), "/tmp/s.svbin"])
    res = {"engine_version": f"dovecot {ver}", "compile": "ok" if rc == 0 else "refused",
           "compile_class": None if rc == 0 else compile_class(txt), "compile_text": txt.strip()[-300:]}
    if rc != 0:
        return res
    maildir = Path("/tmp/Maildir")
    make_maildir(maildir, meta.get("folders", ["INBOX"]))
    Path("/tmp/pd-home").mkdir(exist_ok=True)
    conf = ph_conf(ver, maildir)
    env_args = ["-f", meta.get("from", "sender@example.invalid"), "-r", meta.get("to", "user@lab.test")]
    rc, txt = sh(["sieve-test", "-c", str(conf), *env_args, str(s), str(m)])
    intent, section = [], None
    for line in txt.splitlines():
        if "Performed actions" in line:
            section = "performed"
        elif "Implicit keep" in line:
            section = "implicit"
        mm = re.match(r"\s*\*\s*(.+)", line)
        if mm and section:
            item = mm.group(1)
            for pat, f in PH_ACTION:
                x = re.search(pat, item, re.I)
                if x:
                    a = f(x)
                    intent.append("keep" if section == "implicit" and a == "fileinto:INBOX" else a)
                    break
            else:
                intent.append("other:" + item[:60])
    res["intent"] = sorted(set(intent))
    res["intent_rc"] = rc
    rc, txt = sh(["sieve-test", "-c", str(conf), "-e", *env_args, str(s), str(m)])
    res["effect"] = {"stored": maildir_counts(maildir), "emitted": emitted(), "rc": rc,
                     "result": (re.findall(r"final result:\s*(.+)", txt) or [""])[-1].strip()}
    return res


# ---------------------------------------------------------------- GNU Mailutils
def mailutils(fx: Path, meta: dict) -> dict:
    ver = sh(["sieve", "--version"])[1].splitlines()[0]
    s = Path("/tmp/s.sieve")
    shutil.copy(fx / "script.sieve", s)
    rc, txt = sh(["sieve", "--compile-only", str(s)])
    res = {"engine_version": ver, "compile": "ok" if rc == 0 else "refused",
           "compile_class": None if rc == 0 else compile_class(txt), "compile_text": txt.strip()[-300:]}
    if rc != 0:
        return res
    # source mailbox: one-message maildir; fileinto targets: maildir folders under /tmp/mbox
    src = Path("/tmp/src")
    for sub in ("cur", "new", "tmp"):
        (src / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(fx / "msg.eml", src / "new" / "1.lab")
    root = Path("/tmp/mbox")
    for f in meta.get("folders", ["INBOX"]):
        if f != "INBOX":
            for sub in ("cur", "new", "tmp"):
                (root / f / sub).mkdir(parents=True, exist_ok=True)
    common = ["--mbox-url", f"maildir://{src}", "--mailer-url", "prog:///lab/capture.sh",
              "--email", meta.get("to", "user@lab.test"), "-v"]
    rc, txt = sh(["sieve", "--dry-run", *common, str(s)], env={"HOME": "/tmp/mbox", "FOLDER": "/tmp/mbox"})
    intent = []
    for line in txt.splitlines():
        low = line.lower()
        for key, tag in (("fileinto", "fileinto"), ("redirect", "redirect"), ("reject", "reject"),
                         ("discard", "discard"), ("vacation", "vacation"), ("keep", "keep")):
            if re.search(rf"\b{key}\b", low):
                tgt = re.findall(r"(?:delivering into|to)\s+(\S+)", line)
                intent.append(f"{tag}:{tgt[0]}" if tag in ("fileinto", "redirect") and tgt else tag)
                break
    res["intent"] = sorted(set(intent)) or ["keep"]
    res["intent_text"] = txt.strip()[-400:]
    rc, txt = sh(["sieve", *common, str(s)], env={"HOME": "/tmp/mbox", "FOLDER": "/tmp/mbox"})
    def live(x: Path) -> bool:  # maildir flag T = deleted (discard, or no keep after fileinto)
        return x.is_file() and "T" not in (x.name.split(":2,", 1)[1] if ":2," in x.name else "")
    stored = {"INBOX": sum(1 for sub in ("cur", "new") for x in (src / sub).glob("*") if live(x))}
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        stored["INBOX." + d.name if not d.name.startswith("INBOX") else d.name] = sum(
            1 for sub in ("cur", "new") for x in (d / sub).glob("*") if x.is_file())
    res["effect"] = {"stored": stored, "emitted": emitted(), "rc": rc, "text": txt.strip()[-300:]}
    return res


# ---------------------------------------------------------------- Exim (filter test mode)
def exim(fx: Path, meta: dict) -> dict:
    ver = sh(["exim", "-bV"])[1].splitlines()[0]
    rc, txt = sh(["exim", "-bf", str(fx / "script.sieve"), "-f", meta.get("from", "sender@example.invalid")],
                 stdin=(fx / "msg.eml").read_bytes())
    res = {"engine_version": ver}
    if re.search(r"sieve error|filter error|error in sieve", txt, re.I):
        res.update(compile="refused", compile_class=compile_class(txt), compile_text=txt.strip()[-300:])
        return res
    res["compile"] = "ok"
    intent = []
    for line in txt.splitlines():
        x = re.match(r"\s*Save message to:\s*(\S+)", line)
        if x:
            intent.append(f"fileinto:{x.group(1)}")
        x = re.match(r"\s*Deliver message to:\s*(\S+)", line)
        if x:
            intent.append(f"redirect:{x.group(1)}")
        if re.search(r"Discard|Filtering did not set up a significant delivery", line, re.I):
            intent.append("discard" if "Discard" in line else "keep-implicit")
        if re.search(r"Normal delivery will occur", line, re.I):
            intent.append("keep")
        if re.search(r"Sieve vacation|vacation", line, re.I) and "Sieve" in line:
            intent.append("vacation")
    res["intent"] = sorted(set(intent)) or ["keep"]
    res["intent_text"] = txt.strip()[-400:]
    return res


# ---------------------------------------------------------------- library runners (JSON already)
def runner(cmd: str):
    def f(fx: Path, meta: dict) -> dict:
        rc, txt = sh([cmd, str(fx / "script.sieve"), str(fx / "msg.eml"), meta.get("from", "sender@example.invalid"),
                      meta.get("to", "user@lab.test")])
        line = next((ln for ln in reversed(txt.splitlines()) if ln.startswith("{")), None)
        if not line:
            return {"compile": "refused", "compile_class": "other", "compile_text": txt.strip()[-300:],
                    "runner_error": True}
        r = json.loads(line)
        if r.get("compile") == "refused" and not r.get("compile_class"):
            r["compile_class"] = compile_class(r.get("compile_text", ""))
        return r
    return f


ENGINES = {"pigeonhole": pigeonhole, "mailutils": mailutils, "exim": exim,
           "sieve-rs": runner("pd-sieve-rs"), "jsieve": runner("pd-jsieve")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--fx", required=True)
    a = ap.parse_args()
    fx = Path(a.fx)
    meta = json.loads((fx / "meta.json").read_text())
    try:
        res = ENGINES[a.engine](fx, meta)
    except Exception as e:  # noqa: BLE001
        res = {"harness_error": repr(e)[:300]}
    print(json.dumps(res, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
