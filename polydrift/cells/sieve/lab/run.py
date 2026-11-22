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
intent only), jsieve (pd-jsieve runner, intent only), pigeonhole-lmtp (phase 2: compile and intent as
pigeonhole, effect from a real delivery through Dovecot's LMTP server, with the reply to DATA), cyrus (phase 2:
sievec compile, then a real delivery through Cyrus lmtpd; effect records only, Cyrus has no dry run), stalwart
(phase 2: sieve-rs inside Stalwart; compile from JMAP SieveScript/set, then a real delivery over LMTP; effect
only, outgoing mail relayed to an in-container sink).
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


def sh(args: list[str], stdin: bytes | None = None, timeout: int = 60, env: dict | None = None,
       cwd: str | None = None) -> tuple[int, str]:
    r = subprocess.run(args, input=stdin, capture_output=True, timeout=timeout, env={**os.environ, **(env or {})},
                       cwd=cwd)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def compile_class(text: str) -> str:
    t = text.lower()
    if re.search(r"unknown (extension|capability)|unsupported (extension|capability)|not (supported|available)|"
                 r"require.*(unknown|unsupported|not supported)|extension .* is not", t):
        return "unknown-extension"
    if re.search(r"syntax|parse|unexpected|expected|missing|invalid", t):
        return "syntax"
    return "other"


ORIG: dict = {}   # facts of the message being filtered, set by main() before an engine runs


def _part_field(m, ctype: str, name: str) -> str | None:
    """Value of field ``name`` in the first MIME part of type ``ctype`` (MDN disposition, DSN action)."""
    for part in m.walk() if m.is_multipart() else []:
        if part.get_content_type() == ctype:
            payload = part.get_payload()
            text = payload if isinstance(payload, str) else "".join(
                str(x) for x in (payload if isinstance(payload, list) else [payload]))
            mm = re.search(rf"^{name}:\s*(.+)$", text, re.I | re.M)
            if mm:
                return " ".join(mm.group(1).split()).lower()
    return None


def facts(m, args: list[str]) -> dict:
    return {**_facts(m, args), "shape": {"multipart_report": (m.get_content_type() or "").lower() == "multipart/report",
                                          "parts": _part_types(m)}}


def _facts(m, args: list[str]) -> dict:
    """Header facts of a sent message that the Sieve RFCs fix (RFC 5228 4.2, RFC 5230 5, RFC 5429 2.1 and 2.2)."""
    env_from = None
    if "-f" in args and args.index("-f") + 1 < len(args):
        env_from = args[args.index("-f") + 1].strip("<>")
    orig_mid = ORIG.get("message_id")
    refs = (m.get("References") or "")
    return {
        "received_delta": len(m.get_all("Received") or []) - ORIG.get("received", 0),
        "in_reply_to_orig": bool(orig_mid) and orig_mid in (m.get("In-Reply-To") or ""),
        "references_orig": bool(orig_mid) and orig_mid in refs,
        "subject": " ".join((m.get("Subject") or "").split()) or None,
        "from": " ".join((m.get("From") or "").split()) or None,
        "to": " ".join((m.get("To") or "").split()) or None,
        "date_present": m.get("Date") is not None,
        "env_from": env_from,
        "mdn_disposition": _part_field(m, "message/disposition-notification", "Disposition"),
        "dsn_action": _part_field(m, "message/delivery-status", "Action"),
        # fixtures put PD-MARKER into a reject/ereject reason: is it in the human-readable text that went out
        "marker": "PD-MARKER" in m.as_string(),
        # RFC 5230 5.1: NOTIFY=NEVER on the RCPT (sendmail -N never, or the SMTP RCPT parameter the sink keeps)
        "notify_never": _notify(args) == "never",
        # RFC 5429 2.2.1: the first (human-readable) part says more than the reason itself
        "text_beyond_reason": _text_beyond_reason(m),
        # RFC 5429 2.4: an addheader in the script ("X-PD: added") shows in the notice's copy of the message
        "notice_has_script_edit": _notice_has_edit(m),
    }


def _notify(args: list[str]):
    for i, a in enumerate(args):
        if a == "-N" and i + 1 < len(args):
            return args[i + 1].strip().lower()
        if a.startswith("-N") and len(a) > 2:
            return a[2:].strip().lower()
    return None


def _first_text(m):
    for p in (m.walk() if m.is_multipart() else [m]):
        if p.get_content_maintype() == "text" and not p.is_multipart():
            pl = p.get_payload(decode=True) or b""
            return pl.decode(p.get_content_charset() or "utf-8", "replace")
    return None


def _text_beyond_reason(m):
    t = _first_text(m)
    if t is None or "PD-MARKER" not in t:
        return None
    rest = t.replace("PD-MARKER refused by policy", "").replace("PD-MARKER", "")
    return any(ch.isalnum() for ch in rest)


def _notice_has_edit(m):
    if not m.is_multipart():
        return None
    for p in m.walk():
        ct = p.get_content_type().lower()
        if ct in ("message/rfc822", "text/rfc822-headers", "message/rfc822-headers"):
            return "X-PD: added" in p.as_string() or "x-pd: added" in p.as_string().lower()
    return None


def emitted() -> list[dict]:
    out = []
    for f in sorted(OUT.glob("*.eml"), key=lambda p: int(p.stem)):
        args = (OUT / f"{f.stem}.args").read_text(errors="replace").split("\n") if (OUT / f"{f.stem}.args").exists() else []
        m = email.message_from_bytes(f.read_bytes(), policy=policy.compat32)
        ctype = (m.get("Content-Type") or "").lower()
        auto = (m.get("Auto-Submitted") or "").lower()
        kind = classify(m)
        rcpt = sorted(a for a in args if "@" in a and not a.startswith("-"))
        if not rcpt:
            rcpt = sorted(x.strip().strip("<>") for x in (m.get("To") or "").split(",") if "@" in x)
        out.append({"kind": kind, "to": rcpt, "auto_submitted": auto or None, "facts": facts(m, args)})
    return out


def _part_types(m) -> list[str]:
    return [p.get_content_type().lower() for p in m.walk()] if m.is_multipart() else [m.get_content_type().lower()]


def classify(m) -> str:
    """Kind of a message the engine sent. A rejection is recognised by what it carries, not only by the MDN form
    RFC 5429 asks for: a message/disposition-notification part is an MDN reject; a message/delivery-status part is a
    DSN, and a DSN-shaped message that returns the original after a reject action is still the reject's message
    (GNU Mailutils sends multipart/mixed with a delivery-status part). The shape is kept apart in the facts."""
    ctype = (m.get("Content-Type") or "").lower()
    auto = (m.get("Auto-Submitted") or "").lower()
    parts = _part_types(m)
    if "disposition-notification" in ctype or "report-type=disposition" in ctype \
            or "message/disposition-notification" in parts:
        return "reject"
    if "delivery-status" in ctype or "message/delivery-status" in parts:
        return "dsn"
    if auto.startswith("auto-replied") or m.get("X-Sieve-Vacation"):
        return "vacation"
    if m.get("X-Sieve-Redirected-From") or (m.get("Message-ID") and not auto):
        return "redirect"
    return "other"


def emitted_from_text(txt: str) -> list[dict]:
    """sieve-test -e prints each outgoing message (starting with an X-Sieve: header) and a RECIPIENT: line for its
    envelope recipient instead of sending it; pair them in order."""
    lines = txt.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith("X-Sieve:")]
    rcpts = [ln.split(":", 1)[1].strip() for ln in lines if ln.startswith("RECIPIENT:")]
    out = []
    for k, i in enumerate(starts):
        end = next((j for j in range(i, len(lines)) if lines[j] == ""), len(lines))
        m = email.message_from_string("\n".join(lines[i:end]) + "\n\n", policy=policy.compat32)
        to = [rcpts[k]] if k < len(rcpts) else sorted(x.strip().strip("<>") for x in (m.get("To") or "").split(",") if "@" in x)
        out.append({"kind": classify(m), "to": sorted(to), "auto_submitted": (m.get("Auto-Submitted") or "").lower() or None,
                    "facts": facts(m, [])})
    return out


def maildir_counts(root: Path) -> dict[str, int]:
    counts = {}
    for d in [root, *sorted(p for p in root.glob(".*") if p.is_dir() and p.name not in (".", ".."))]:
        if not (d / "cur").exists() and not (d / "new").exists():
            continue
        name = "INBOX" if d == root else "INBOX" + d.name
        counts[name] = sum(1 for sub in ("cur", "new") for x in (d / sub).glob("*") if x.is_file())
    return counts


def maildir_flags(root: Path) -> dict[str, list[str]]:
    """Per folder, the IMAP flags of each stored message (maildir info letters and Dovecot keyword letters mapped
    through dovecot-keywords), each message's flags as one sorted space-separated string (RFC 5232 effect)."""
    letters = {"F": "\\Flagged", "S": "\\Seen", "R": "\\Answered", "D": "\\Draft", "T": "\\Deleted"}
    out = {}
    for d in [root, *sorted(p for p in root.glob(".*") if p.is_dir() and p.name not in (".", ".."))]:
        if not (d / "cur").exists() and not (d / "new").exists():
            continue
        kw = {}
        kf = d / "dovecot-keywords"
        if kf.exists():
            for ln in kf.read_text(errors="replace").splitlines():
                n, _, word = ln.partition(" ")
                if n.isdigit():
                    kw[chr(ord("a") + int(n))] = word
        name = "INBOX" if d == root else "INBOX" + d.name
        msgs = []
        for sub in ("cur", "new"):
            for x in sorted((d / sub).glob("*")):
                info = x.name.split(":2,", 1)[1] if ":2," in x.name else ""
                fl = sorted({letters.get(c) or kw.get(c, "?" + c) for c in info}
                            - {"$HasAttachment", "$HasNoAttachment"})
                msgs.append(" ".join(fl))
        if msgs:
            out[name] = sorted(msgs)
    return out


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
    # The lab builds Courier-style folders (.Quarantine is INBOX.Quarantine), so Dovecot gets the matching
    # namespace: prefix INBOX., separator ".". Without it .Quarantine is the mailbox "Quarantine".
    c = Path("/tmp/pd-dovecot.conf")
    if ver.startswith("2.4"):
        c.write_text(f"""dovecot_config_version = 2.4.0
dovecot_storage_version = 2.4.0
mail_driver = maildir
mail_path = {maildir}
mail_inbox_path = {maildir}
namespace inbox {{
  inbox = yes
  prefix = INBOX.
  separator = .
}}
mail_home = /tmp/pd-home
mail_uid = 1000
mail_gid = 1000
postmaster_address = postmaster@lab.test
sendmail_path = /lab/capture.sh
""")
    else:
        c.write_text(f"""mail_location = maildir:{maildir}
namespace inbox {{
  inbox = yes
  prefix = INBOX.
  separator = .
}}
mail_home = /tmp/pd-home
mail_uid = 1000
mail_gid = 1000
postmaster_address = postmaster@lab.test
sendmail_path = /lab/capture.sh
""")
    return c


def pigeonhole(fx: Path, meta: dict) -> dict:
    ver = sh(["dovecot", "--version"])[1].split()[0]
    s, m = Path("/tmp/s.sieve"), Path("/tmp/m.eml")
    shutil.copy(fx / "script.sieve", s)
    shutil.copy(fx / "msg.eml", m)
    maildir = Path("/tmp/Maildir")
    make_maildir(maildir, meta.get("folders", ["INBOX"]))
    Path("/tmp/pd-home").mkdir(exist_ok=True)
    # sieve-test runs as mail_uid 1000 and writes Dovecot index files in the maildir and home.
    for p in (maildir, Path("/tmp/pd-home")):
        sh(["chown", "-R", "1000:1000", str(p)])
    conf = ph_conf(ver, maildir)
    rc, txt = sh(["sievec", "-c", str(conf), str(s), "/tmp/s.svbin"])
    res = {"engine_version": f"dovecot {ver}", "compile": "ok" if rc == 0 else "refused",
           "compile_class": None if rc == 0 else compile_class(txt), "compile_text": txt.strip()[-300:]}
    if rc != 0:
        return res
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
    if rc != 0:
        res["runtime_error"] = " | ".join(ln.strip() for ln in txt.splitlines() if "error" in ln.lower())[-300:] or f"rc {rc}"
    rc, txt = sh(["sieve-test", "-c", str(conf), "-e", *env_args, str(s), str(m)])
    res["effect"] = {"stored": maildir_counts(maildir), "flags": maildir_flags(maildir),
                     "emitted": emitted() + emitted_from_text(txt), "rc": rc,
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
    common = ["--no-config", "--mbox-url", f"maildir://{src}", "--mailer", "prog:///lab/capture.sh",
              "--email", meta.get("to", "user@lab.test"), "-v"]
    root.mkdir(parents=True, exist_ok=True)
    rc, txt = sh(["sieve", "--dry-run", *common, str(s)], env={"HOME": "/tmp/mbox"}, cwd=str(root))
    intent = []
    for line in txt.splitlines():
        low = line.lower()
        for key, tag in (("fileinto", "fileinto"), ("redirect", "redirect"), ("reject", "reject"),
                         ("discard", "discard"), ("vacation", "vacation"), ("keep", "keep")):
            if re.search(rf"\b{key}\b", low):
                tgt = re.findall(r"(?:delivering into|to)\s+(\S+)", line)
                intent.append(f"{tag}:{tgt[0]}" if tag in ("fileinto", "redirect") and tgt else tag)
                break
    res["intent"] = sorted(set(intent)) if rc == 0 else None
    res["intent_rc"] = rc
    res["intent_text"] = txt.strip()[-400:]
    rc, txt = sh(["sieve", *common, str(s)], env={"HOME": "/tmp/mbox"}, cwd=str(root))
    def live(x: Path) -> bool:  # maildir flag T = deleted (discard, or no keep after fileinto)
        return x.is_file() and "T" not in (x.name.split(":2,", 1)[1] if ":2," in x.name else "")
    stored = {"INBOX": sum(1 for sub in ("cur", "new") for x in (src / sub).glob("*") if live(x))}
    for d in sorted(root.iterdir()):
        name = d.name if d.name.startswith("INBOX") else "INBOX." + d.name
        if d.is_dir():  # maildir folder made by the lab (meta folders) or by the engine
            stored[name] = sum(1 for sub in ("cur", "new") for x in (d / sub).glob("*") if x.is_file())
        elif d.is_file():  # mbox file the engine created for a fileinto target
            stored[name] = sum(1 for ln in d.read_bytes().split(b"\n") if ln.startswith(b"From "))
    res["effect"] = {"stored": stored, "emitted": emitted(), "rc": rc, "text": txt.strip()[-300:]}
    return res


# ---------------------------------------------------------------- Exim (filter test mode)
def exim(fx: Path, meta: dict) -> dict:
    ver = sh(["exim", "-bV"])[1].splitlines()[0]
    # Exim treats a filter file as Sieve only when its first line is "# Sieve filter" (Exim spec, filter chapter)
    s = Path("/tmp/s.sieve")
    s.write_bytes(b"# Sieve filter" + bytes([10]) + (fx / "script.sieve").read_bytes())
    to = meta.get("to", "user@lab.test")
    rc, txt = sh(["exim", "-v", "-bf", str(s), "-f", meta.get("from", "sender@example.invalid"),
                  "-bfl", to.split("@")[0], "-bfd", to.split("@")[-1]],
                 stdin=(fx / "msg.eml").read_bytes())
    res = {"engine_version": ver, "intent_rc": rc}
    if re.search(r"sieve error|filter error|error in sieve|error in (forward|filter) file", txt, re.I) or rc != 0:
        res.update(compile="refused", compile_class=compile_class(txt), compile_text=txt.strip()[-300:])
        return res
    res["compile"] = "ok"
    intent = []
    for line in txt.splitlines():
        y = re.search(r"\b(fileinto|redirect)\s+`([^']*)'", line)
        if y:
            intent.append(f"{y.group(1)}:{y.group(2)}")
        if re.search(r"\bdiscard\b", line) and "`" not in line and "Sieve" not in line:
            intent.append("discard")
        if re.search(r"\bkeep\b", line) and "implicit" not in line.lower():
            intent.append("keep")
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
        if re.search(r"\bvacation\b", line, re.I) and not re.search(r"ignore|not personal", line, re.I) \
                and "Sieve" in line:
            intent.append("vacation")
    if not intent and "No implicit keep" not in txt:
        intent.append("keep")
    res["intent"] = sorted(set(intent))
    res["intent_text"] = txt.strip()[-400:]
    return res



# ---------------------------------------------------------------- Pigeonhole over Dovecot LMTP (phase 2)
def lmtp_reply_facts(code: int, text: str) -> dict:
    """The final reply to DATA as the sending MTA sees it: code class, enhanced status code, line count and
    whether every line fits the RFC 5321 4.5.3.1.5 limit (512 octets with CRLF)."""
    lines = (text or "").split("\n") if text else [""]
    enh = next((m for m in (re.match(r"\s*([245]\.\d{1,3}\.\d{1,3})\b", ln) for ln in lines) if m), None)
    return {"code": code, "class": str(code)[0] if code else None, "enhanced": enh.group(1) if enh else None,
            "lines": len(lines), "max_line": max(len(f"{code} {ln}") + 2 for ln in lines),
            "marker": "PD-MARKER" in (text or ""), "text": (text or "")[:600]}


def lmtp_conf(ver: str, maildir: Path, script: Path) -> Path:
    run = Path("/tmp/pd-run")
    run.mkdir(exist_ok=True)
    base = f"""base_dir = {run}
state_dir = /tmp/pd-state
protocols = lmtp
ssl = no
log_path = /tmp/pd-dovecot.log
default_internal_user = pd
default_internal_group = pd
default_login_user = pd
postmaster_address = postmaster@lab.test
hostname = pd-dovecot.lab.test
sendmail_path = /lab/capture.sh
service lmtp {{
  unix_listener lmtp {{
    mode = 0666
  }}
}}
"""
    c = Path("/tmp/pd-lmtp.conf")
    if ver.startswith("2.4"):
        c.write_text(f"""dovecot_config_version = 2.4.0
dovecot_storage_version = 2.4.0
{base}mail_driver = maildir
mail_path = {maildir}
mail_inbox_path = {maildir}
mail_home = /tmp/pd-home
namespace inbox {{
  inbox = yes
  prefix = INBOX.
  separator = .
}}
userdb static {{
  fields {{
    uid = 1000
    gid = 1000
    home = /tmp/pd-home
  }}
}}
passdb static {{
  password = pd
}}
protocol lmtp {{
  mail_plugins {{
    sieve = yes
  }}
}}
sieve_script personal {{
  type = personal
  driver = file
  path = /tmp/pd-sieve
  active_path = {script}
}}
""")
    else:
        c.write_text(f"""{base}mail_location = maildir:{maildir}
mail_home = /tmp/pd-home
namespace inbox {{
  inbox = yes
  prefix = INBOX.
  separator = .
}}
userdb {{
  driver = static
  args = uid=1000 gid=1000 home=/tmp/pd-home
}}
passdb {{
  driver = static
  args = password=pd
}}
protocol lmtp {{
  mail_plugins = $mail_plugins sieve
}}
plugin {{
  sieve = file:/tmp/pd-sieve;active={script}
}}
""")
    return c


def pigeonhole_lmtp(fx: Path, meta: dict) -> dict:
    """The script runs at delivery time inside Dovecot's LMTP server; the reply to DATA is what the sending MTA
    acts on (RFC 5429 2.1: ereject refuses at protocol level, reject sends an MDN after accepting)."""
    import smtplib
    import time
    # compile and intent from the same engine's test tool (as the pigeonhole engine), then a clean lab for the
    # LMTP delivery
    res = pigeonhole(fx, meta)
    res["engine_version"] = res.get("engine_version", "") + " lmtp"
    res.pop("effect", None)
    if res.get("compile") != "ok":
        return res
    shutil.rmtree("/tmp/Maildir", ignore_errors=True)
    shutil.rmtree(OUT, ignore_errors=True)
    ver = sh(["dovecot", "--version"])[1].split()[0]
    s = Path("/tmp/pd-home/.dovecot.sieve")
    for d in ("/tmp/pd-home", "/tmp/pd-sieve", "/tmp/pd-state"):
        Path(d).mkdir(parents=True, exist_ok=True)
    shutil.copy(fx / "script.sieve", s)
    maildir = Path("/tmp/Maildir")
    make_maildir(maildir, meta.get("folders", ["INBOX"]))
    for p in (maildir, Path("/tmp/pd-home"), Path("/tmp/pd-sieve")):
        sh(["chown", "-R", "1000:1000", str(p)])
    conf = lmtp_conf(ver, maildir, s)
    sh(["chown", "-R", "1000:1000", "/tmp/pd-home"])
    # the master daemonises; its children must not inherit the wrapper's pipes, or the wrapper waits on them
    with open("/tmp/pd-dovecot.start", "wb") as so:
        rc = subprocess.run(["dovecot", "-c", str(conf)], stdout=so, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, timeout=30).returncode
    txt = Path("/tmp/pd-dovecot.start").read_text(errors="replace")
    sock = "/tmp/pd-run/lmtp"
    for _ in range(100):
        if Path(sock).exists():
            break
        time.sleep(0.1)
    else:
        log = Path("/tmp/pd-dovecot.log").read_text(errors="replace")[-300:] if Path("/tmp/pd-dovecot.log").exists() else ""
        return {**res, "harness_error": f"lmtp socket missing: {txt.strip()[-150:]} {log}"}
    try:
        c = smtplib.LMTP(sock, timeout=30)
        c.ehlo_or_helo_if_needed()
        c.mail(meta.get("from", "sender@example.invalid"))
        code, msg = c.rcpt(meta.get("to", "user@lab.test"))
        rcpt = {"code": code, "text": msg.decode("utf-8", "replace")[:300]}
        if code >= 400:
            final = (code, msg)
        else:
            c.putcmd("data")
            code, msg = c.getreply()
            if code != 354:
                final = (code, msg)
            else:
                data = (fx / "msg.eml").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                c.send(smtplib._quote_periods(data) + (b"" if data.endswith(b"\r\n") else b"\r\n") + b".\r\n")
                final = c.getreply()
        try:
            c.quit()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        return {**res, "harness_error": f"lmtp session: {e!r}"[:300]}
    finally:
        subprocess.run(["doveadm", "-c", str(conf), "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, timeout=30)
    time.sleep(0.3)
    rep = lmtp_reply_facts(final[0], final[1].decode("utf-8", "replace"))
    res["effect"] = {"stored": maildir_counts(maildir), "flags": maildir_flags(maildir), "emitted": emitted(),
                     "lmtp": rep, "lmtp_rcpt": rcpt}
    log = Path("/tmp/pd-dovecot.log")
    if log.exists():
        res["log_tail"] = log.read_text(errors="replace")[-400:]
    return res



# ---------------------------------------------------------------- Cyrus IMAP over LMTP (phase 2)
CY = Path("/tmp/cy")


def cyrus_conf(folders: list[str]) -> tuple[Path, Path]:
    for d in ("conf", "spool", "sieve", "socket", "run"):
        (CY / d).mkdir(parents=True, exist_ok=True)
    # the services master starts read /etc/imapd.conf, not master -C (throwaway container, so write it there)
    imapd = Path("/etc/imapd.conf")
    imapd.write_text(f"""configdirectory: {CY}/conf
partition-default: {CY}/spool
sievedir: {CY}/sieve
lmtpsocket: {CY}/socket/lmtp
idlesocket: {CY}/socket/idle
notifysocket: {CY}/socket/notify
admins: cyrus
servername: pd-cyrus.lab.test
defaultdomain: lab.test
virtdomains: off
unixhierarchysep: no
altnamespace: no
mailbox_legacy_dirs: yes
sasl_pwcheck_method: auxprop
sasl_auxprop_plugin: sasldb
sasl_mech_list: PLAIN
allowplaintext: yes
sendmail: /lab/capture.sh
postmaster: postmaster
lmtp_downcase_rcpt: yes
syslog_prefix: pd-cyrus
""")
    master = Path("/etc/cyrus.conf")
    master.write_text(f"""START {{
  recover cmd="ctl_cyrusdb -r"
}}
SERVICES {{
  lmtpunix cmd="lmtpd" listen="{CY}/socket/lmtp" prefork=0
  imap cmd="imapd" listen="127.0.0.1:1143" prefork=0
}}
EVENTS {{
}}
""")
    return imapd, master


def mutf7_encode(s: str) -> str:
    """IMAP modified UTF-7 (RFC 3501 5.1.3): how a non-ASCII mailbox name goes over IMAP and how Cyrus stores it."""
    import base64
    out, run = [], []

    def flush() -> None:
        if run:
            b = base64.b64encode("".join(run).encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            out.append("&" + b + "-")
            run.clear()
    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            run.append(ch)
    flush()
    return "".join(out)


def mutf7_decode(s: str) -> str:
    import base64
    def dec(m: re.Match) -> str:
        b = m.group(1)
        if not b:
            return "&"
        b = b.replace(",", "/")
        return base64.b64decode(b + "=" * (-len(b) % 4)).decode("utf-16-be")
    return re.sub(r"&([A-Za-z0-9+,]*)-", dec, s)


def cyrus_store(user: str) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Messages per folder from the legacy spool layout (N. files per folder directory)."""
    # hashimapspool is off in this config: spool/user/<name>; hashed layout spool/<h>/user/<name> as fallback
    root = CY / "spool" / "user" / user
    if not root.exists():
        root = CY / "spool" / user[0] / "user" / user
    counts: dict[str, int] = {}
    if not root.exists():
        return {"INBOX": 0}, {}
    for d in [root] + sorted(p for p in root.rglob("*") if p.is_dir()):
        name = "INBOX" if d == root else "INBOX." + ".".join(mutf7_decode(p) for p in d.relative_to(root).parts)
        counts[name] = sum(1 for f in d.iterdir() if f.is_file() and re.fullmatch(r"\d+\.", f.name))
    return counts, {}


def cyrus(fx: Path, meta: dict) -> dict:
    import smtplib
    import time
    bindir = "/usr/lib/cyrus/bin"
    ver = sh(["dpkg-query", "-W", "-f", "${Version}", "cyrus-imapd"])[1].strip()
    folders = meta.get("folders", ["INBOX"])
    imapd, master = cyrus_conf(folders)
    user = meta.get("to", "user@lab.test").split("@")[0].lower()
    sdir = CY / "sieve" / user[0] / user
    sdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(fx / "script.sieve", sdir / "pd.script")
    rc, txt = sh([f"{bindir}/sievec", "-C", str(imapd), str(sdir / "pd.script"), str(sdir / "pd.bc")])
    res = {"engine_version": f"cyrus-imapd {ver} lmtp", "effect_only": True,
           "compile": "ok" if rc == 0 else "refused", "compile_class": None if rc == 0 else compile_class(txt),
           "compile_text": txt.strip()[-300:]}
    if rc != 0:
        return res
    os.symlink("pd.bc", sdir / "defaultbc")
    # admin password for the mailbox setup, lab only (default sasldb of this throwaway container)
    for realm in ("pd-cyrus.lab.test", os.uname().nodename):
        sh(["saslpasswd2", "-p", "-c", "-u", realm, "cyrus"], stdin=b"pd")
    sh(["chown", "cyrus:mail", "/etc/sasldb2"])
    sh(["chown", "-R", "cyrus:mail", str(CY)])
    log = open("/tmp/pd-cyrus.log", "wb")
    proc = subprocess.Popen([f"{bindir}/master", "-C", str(imapd), "-M", str(master), "-p", str(CY / "run/master.pid")],
                            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    sock = str(CY / "socket" / "lmtp")
    for _ in range(150):
        if Path(sock).exists():
            break
        time.sleep(0.1)
    else:
        proc.kill()
        return {**res, "harness_error": "cyrus lmtp socket missing: " +
                Path("/tmp/pd-cyrus.log").read_text(errors="replace")[-250:]}
    # the lab mailbox and its folders, created by the Cyrus admin over IMAP (test-only password check)
    import imaplib
    try:
        for _ in range(50):
            try:
                im = imaplib.IMAP4("127.0.0.1", 1143)
                break
            except OSError:
                time.sleep(0.1)
        im.login("cyrus", "pd")
        for f in folders:
            name = "user." + user + ("" if f == "INBOX" else "." + mutf7_encode(f.split(".", 1)[1]))
            typ, data = im.create(name)
            if typ != "OK":
                raise RuntimeError(f"create {name}: {data}")
        im.logout()
    except Exception as e:  # noqa: BLE001
        proc.kill()
        return {**res, "harness_error": f"cyrus mailbox setup: {e!r}"[:300]}
    try:
        c = smtplib.LMTP(sock, timeout=30)
        c.ehlo_or_helo_if_needed()
        c.mail(meta.get("from", "sender@example.invalid"))
        code, msg = c.rcpt(meta.get("to", "user@lab.test"))
        rcpt = {"code": code, "text": msg.decode("utf-8", "replace")[:300]}
        if code >= 400:
            final = (code, msg)
        else:
            c.putcmd("data")
            code, msg = c.getreply()
            if code != 354:
                final = (code, msg)
            else:
                data = (fx / "msg.eml").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                c.send(smtplib._quote_periods(data) + (b"" if data.endswith(b"\r\n") else b"\r\n") + b".\r\n")
                final = c.getreply()
        try:
            c.quit()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        proc.kill()
        return {**res, "harness_error": f"lmtp session: {e!r}"[:300]}
    time.sleep(0.5)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    stored, _ = cyrus_store(user)
    for f in folders:
        stored.setdefault(f, 0)
    res["effect"] = {"stored": stored, "emitted": emitted(),
                     "lmtp": lmtp_reply_facts(final[0], final[1].decode("utf-8", "replace")), "lmtp_rcpt": rcpt}
    res["log_tail"] = Path("/tmp/pd-cyrus.log").read_text(errors="replace")[-400:]
    return res


# ---------------------------------------------------------------- Stalwart (sieve-rs inside the server) over LMTP (phase 2)
ST = Path("/tmp/stal")
ST_ADMIN = ("admin", "Lab-Admin-2026")       # lab only: STALWART_RECOVERY_ADMIN of this throwaway container
ST_USER = ("user", "Lab-User-2026")
SINK_PORT = 2525
KEYWORD_FLAG = {"$seen": "\\Seen", "$flagged": "\\Flagged", "$answered": "\\Answered", "$draft": "\\Draft"}
SERVER_KEYWORDS = {"$hasattachment", "$hasnoattachment"}  # set by Stalwart on ingest, not by the script


class SmtpSink:
    """The lab's only next hop: every message Stalwart sends out (redirect, vacation, reject MDN, DSN) is relayed
    here and kept as N.eml with N.args ("-f", sender, recipients), the format capture.sh writes for the other
    engines, so emitted() reads both the same way. Nothing leaves the container (no network)."""

    def __init__(self, port: int = SINK_PORT):
        import socket
        import threading
        OUT.mkdir(parents=True, exist_ok=True)
        self.n = 0
        self.last = 0.0
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen(8)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        import threading
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(c,), daemon=True).start()

    def _session(self, c) -> None:
        import time
        f = c.makefile("rb")
        send = lambda s: c.sendall(s.encode() + b"\r\n")  # noqa: E731
        send("220 pd-sink ESMTP")
        mfrom, rcpts = "", []
        try:
            while True:
                line = f.readline()
                if not line:
                    return
                cmd = line.decode("utf-8", "replace").strip()
                up = cmd.upper()
                if up.startswith("EHLO"):
                    c.sendall(b"250-pd-sink\r\n250 DSN\r\n")  # NOTARY (RFC 3461) offered: NOTIFY=NEVER applies
                elif up.startswith("HELO"):
                    send("250 pd-sink")
                elif up.startswith("MAIL FROM:"):
                    mfrom, rcpts, notify = (cmd[10:].split()[0].strip("<>") if cmd[10:].strip() else ""), [], []
                    send("250 OK")
                elif up.startswith("RCPT TO:"):
                    rcpts.append(cmd[8:].split()[0].strip("<>"))
                    notify += [p.split("=", 1)[1] for p in cmd[8:].split()[1:] if p.upper().startswith("NOTIFY=")]
                    send("250 OK")
                elif up == "DATA":
                    send("354 go")
                    body = []
                    while True:
                        ln = f.readline()
                        if not ln or ln in (b".\r\n", b".\n"):
                            break
                        body.append(ln[1:] if ln.startswith(b"..") else ln)
                    n, self.n = self.n, self.n + 1
                    (OUT / f"{n}.eml").write_bytes(b"".join(body))
                    (OUT / f"{n}.args").write_text("\n".join(["-f", mfrom, *(["-N", notify[0]] if notify else []), *rcpts]))
                    self.last = time.time()
                    send("250 OK queued")
                elif up == "QUIT":
                    send("221 bye")
                    return
                else:
                    send("250 OK")
        finally:
            c.close()


def _st_http(method: str, path: str, auth: tuple[str, str], body: bytes | None = None,
             ctype: str = "application/json") -> tuple[int, dict | str]:
    import base64
    import urllib.error
    import urllib.request
    h = {"Authorization": "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()}
    if body is not None:
        h["Content-Type"] = ctype
    r = urllib.request.Request("http://127.0.0.1:8080" + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read().decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        raw, code = e.read().decode("utf-8", "replace"), e.code
    try:
        return code, json.loads(raw)
    except ValueError:
        return code, raw


def _st_jmap(auth: tuple[str, str], calls: list, using: list[str]) -> list:
    code, res = _st_http("POST", "/jmap/", auth, json.dumps({"using": using, "methodCalls": calls}).encode())
    if not isinstance(res, dict) or "methodResponses" not in res:
        raise RuntimeError(f"jmap {code}: {str(res)[:200]}")
    return res["methodResponses"]


def _st_admin(calls: list) -> list:
    out = _st_jmap(ST_ADMIN, calls, ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"])
    for name, val, _ in out:
        if name == "error" or val.get("notCreated") or val.get("notUpdated"):
            raise RuntimeError(f"stalwart admin {name}: {json.dumps(val)[:300]}")
    return out


def _st_start(log) -> "subprocess.Popen":
    import time
    env = {**os.environ, "STALWART_RECOVERY_ADMIN": f"{ST_ADMIN[0]}:{ST_ADMIN[1]}"}
    p = subprocess.Popen(["stalwart", "--config", str(ST / "config.json")], cwd=str(ST), env=env, stdout=log,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    for _ in range(900):  # 90 s: three of 1,380 x5 starts (16 Nov, Testbed B under load) needed more than 30 s
        if p.poll() is not None:
            raise RuntimeError(f"stalwart exited {p.returncode}")
        try:
            if _st_http("GET", "/.well-known/jmap", ST_ADMIN)[0] in (200, 307):
                return p
        except OSError:
            pass
        time.sleep(0.1)
    p.kill()
    raise RuntimeError("stalwart http not up in 90 s")


def _st_stop(p) -> None:
    p.terminate()
    try:
        p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(timeout=5)


def _st_configure() -> None:
    """Lab settings through the server's own management methods (Stalwart >= 0.16 keeps its configuration in the
    store; read 14 Nov 2026 from x:*/get on v1.1.0): the domain and the user; an LMTP listener on port 24 without
    TLS; a relay route to the sink chosen for every non-local recipient (the default MX route needs DNS, and the
    container has no network); the spam filter off, so a message lands where the user's script puts it (spam
    classification is not Sieve behaviour); no SASL on the LMTP port (the default asks for it on every port but 25;
    LMTP is the hop from the lab's trusted MTA, as with Dovecot and Cyrus)."""
    out = _st_admin([["x:Domain/set", {"create": {"d1": {"name": "lab.test"}}}, "c0"]])
    dom = out[0][1]["created"]["d1"]["id"]
    # the relay route's host field is "host" in 1.x and "address" in 0.16 (read from each server's own error)
    route = _st_jmap(ST_ADMIN, [["x:MtaRoute/set", {"create": {"r1": {"@type": "Relay", "name": "sink", "host": "127.0.0.1",
                                                                      "port": SINK_PORT}}}, "c3"]],
                     ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"])[0][1]
    if (route.get("notCreated") or {}).get("r1", {}).get("properties") == ["host"]:
        _st_admin([["x:MtaRoute/set", {"create": {"r1": {"@type": "Relay", "name": "sink", "address": "127.0.0.1",
                                                         "port": SINK_PORT}}}, "c3"]])
    elif route.get("notCreated"):
        raise RuntimeError(f"stalwart admin x:MtaRoute/set: {json.dumps(route)[:300]}")
    _st_admin([
        ["x:Account/set", {"create": {"a1": {"@type": "User", "name": ST_USER[0], "domainId": dom, "credentials": {
            "0": {"@type": "Password", "secret": ST_USER[1]}}}}}, "c1"],
        ["x:NetworkListener/set", {"create": {"l1": {"name": "lmtp", "bind": {"[::]:24": True}, "protocol": "lmtp",
                                                    "useTls": False}}}, "c2"],
        ["x:MtaOutboundStrategy/set", {"update": {"singleton": {"route": {
            "match": {"0": {"if": "is_local_domain(rcpt_domain)", "then": "'local'"}}, "else": "'sink'"}}}}, "c4"],
        ["x:MtaStageData/set", {"update": {"singleton": {"enableSpamFilter": {"match": {}, "else": "false"}}}}, "c5"],
        ["x:MtaStageAuth/set", {"update": {"singleton": {"require": {
            "match": {"0": {"if": "local_port == 24", "then": "false"}}, "else": "local_port != 25"}}}}, "c6"],
    ])


def _st_mailboxes(acct: str, auth: tuple[str, str]) -> list[dict]:
    out = _st_jmap(auth, [["Mailbox/get", {"accountId": acct, "ids": None,
                                           "properties": ["name", "role", "parentId", "totalEmails"]}, "m0"]],
                   ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"])
    return out[0][1]["list"]


def _st_name(mb: dict, by_id: dict) -> str:
    """IMAP-style name of a mailbox: INBOX for the inbox role, else the path joined with the server's "/"."""
    if mb.get("role") == "inbox":
        return "INBOX"
    parts, cur = [], mb
    while cur:
        parts.append("INBOX" if cur.get("role") == "inbox" else cur["name"])
        cur = by_id.get(cur.get("parentId"))
    return "/".join(reversed(parts))


def stalwart(fx: Path, meta: dict) -> dict:
    """sieve-rs as Stalwart runs it at delivery time (the user's active script, untrusted runtime). Effect only:
    the server has no dry run. Compile is the server's answer to JMAP SieveScript/set (RFC 9661: invalidSieve)."""
    import smtplib
    import time
    ver = sh(["stalwart", "--version"])[1].strip().split()[-1]
    res = {"engine_version": f"stalwart {ver} lmtp", "effect_only": True}
    shutil.rmtree(ST, ignore_errors=True)
    shutil.rmtree(OUT, ignore_errors=True)
    (ST / "db").mkdir(parents=True)
    (ST / "config.json").write_text(json.dumps({"@type": "RocksDb", "path": str(ST / "db") + "/"}))
    log = open("/tmp/pd-stalwart.log", "wb")
    proc = None
    try:
        proc = _st_start(log)
        _st_configure()
        _st_stop(proc)                         # listeners bind at start: restart for the LMTP listener
        proc = _st_start(log)
        auth = (f"{ST_USER[0]}@lab.test", ST_USER[1])
        code, sess = _st_http("GET", "/jmap/session", auth)
        if not isinstance(sess, dict):
            raise RuntimeError(f"user session {code}: {str(sess)[:150]}")
        acct = sess["primaryAccounts"]["urn:ietf:params:jmap:mail"]
        mail = ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"]
        boxes = _st_mailboxes(acct, auth)
        # the lab folders: INBOX.X as a mailbox named "INBOX.X" at top level (the IMAP name the fixture uses, with
        # the server's "/" delimiter); where fileinto "INBOX.X" puts the message is the engine's behaviour
        create = {f"f{i}": {"name": f, "parentId": None} for i, f in enumerate(meta.get("folders", ["INBOX"]))
                  if f != "INBOX"}
        if create:
            out = _st_jmap(auth, [["Mailbox/set", {"accountId": acct, "create": create}, "m1"]], mail)
            if out[0][1].get("notCreated"):
                raise RuntimeError(f"mailbox create: {json.dumps(out[0][1]['notCreated'])[:200]}")
        code, up = _st_http("POST", f"/jmap/upload/{acct}/", auth, (fx / "script.sieve").read_bytes(),
                            "application/sieve")
        if not isinstance(up, dict) or "blobId" not in up:
            raise RuntimeError(f"script upload {code}: {str(up)[:150]}")
        out = _st_jmap(auth, [["SieveScript/set", {"accountId": acct, "create": {"s1": {"name": "pd", "blobId": up["blobId"]}},
                                                   "onSuccessActivateScript": "#s1"}, "s0"]],
                       ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:sieve"])
        nc = out[0][1].get("notCreated") if out[0][0] == "SieveScript/set" else {"s1": out[0][1]}
        if nc:
            err = nc.get("s1", {})
            txt = f"{err.get('type', '')}: {err.get('description', '')}"
            res.update(compile="refused", compile_class=compile_class(txt), compile_text=txt.strip()[-300:])
            if err.get("type") not in ("invalidSieve", "invalidScript"):   # RFC 9661 name; v1.1.0 says invalidScript
                res["harness_error"] = f"sieve upload: {txt[:200]}"
            return res
        res.update(compile="ok", compile_class=None, compile_text="")
        sink = SmtpSink()
        c = smtplib.LMTP("127.0.0.1", 24, timeout=30)
        c.ehlo_or_helo_if_needed()
        code, msg = c.mail(meta.get("from", "sender@example.invalid"))
        mail_reply = {"code": code, "text": msg.decode("utf-8", "replace")[:300]}
        rcpt = None
        if code < 400:
            code, msg = c.rcpt(meta.get("to", "user@lab.test"))
            rcpt = {"code": code, "text": msg.decode("utf-8", "replace")[:300]}
        if code >= 400:
            final = (code, msg)
        else:
            c.putcmd("data")
            code, msg = c.getreply()
            if code != 354:
                final = (code, msg)
            else:
                data = (fx / "msg.eml").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                c.send(smtplib._quote_periods(data) + (b"" if data.endswith(b"\r\n") else b"\r\n") + b".\r\n")
                final = c.getreply()
        try:
            c.quit()
        except Exception:  # noqa: BLE001
            pass
        # delivery and anything sent out run from the server's queues: wait until the mailboxes and the sink have
        # been still for 2 s (at most 20 s)
        t0, seen, still = time.time(), None, time.time()
        while time.time() - t0 < 20:
            now = (tuple(sorted((b["id"], b["totalEmails"]) for b in _st_mailboxes(acct, auth))), sink.n)
            if now != seen:
                seen, still = now, time.time()
            elif time.time() - still >= 2:
                break
            time.sleep(0.25)
        boxes = _st_mailboxes(acct, auth)
        by_id = {b["id"]: b for b in boxes}
        stored = {f: 0 for f in meta.get("folders", ["INBOX"])}
        flags: dict[str, list[str]] = {}
        for b in boxes:
            name = _st_name(b, by_id)
            if b["totalEmails"] or name in stored:
                stored[name] = b["totalEmails"]
            if b["totalEmails"]:
                q = _st_jmap(auth, [["Email/query", {"accountId": acct, "filter": {"inMailbox": b["id"]}}, "q0"],
                                    ["Email/get", {"accountId": acct, "#ids": {"resultOf": "q0", "name": "Email/query",
                                                                               "path": "/ids"},
                                                   "properties": ["keywords"]}, "g0"]], mail)
                flags[name] = sorted(" ".join(sorted(KEYWORD_FLAG.get(k, k) for k, v in (e.get("keywords") or {}).items()
                                                     if v and k not in SERVER_KEYWORDS))
                                     for e in q[1][1]["list"])
        res["effect"] = {"stored": stored, "flags": flags, "emitted": emitted(),
                         "lmtp": lmtp_reply_facts(final[0], final[1].decode("utf-8", "replace")), "lmtp_rcpt": rcpt,
                         "lmtp_mail": mail_reply}
    except Exception as e:  # noqa: BLE001
        res["harness_error"] = f"stalwart: {e!r}"[:300]
    finally:
        if proc is not None and proc.poll() is None:
            _st_stop(proc)
        log.close()
        res["log_tail"] = Path("/tmp/pd-stalwart.log").read_text(errors="replace")[-400:]
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


ENGINES = {"pigeonhole": pigeonhole, "pigeonhole-lmtp": pigeonhole_lmtp, "cyrus": cyrus, "mailutils": mailutils, "exim": exim,
           "sieve-rs": runner("pd-sieve-rs"), "jsieve": runner("pd-jsieve"), "stalwart": stalwart}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--fx", required=True)
    a = ap.parse_args()
    fx = Path(a.fx)
    meta = json.loads((fx / "meta.json").read_text())
    orig = email.message_from_bytes((fx / "msg.eml").read_bytes(), policy=policy.compat32)
    ORIG.update(message_id=(orig.get("Message-ID") or "").strip() or None,
                received=len(orig.get_all("Received") or []))
    try:
        res = ENGINES[a.engine](fx, meta)
    except Exception as e:  # noqa: BLE001
        res = {"harness_error": repr(e)[:300]}
    # keep is filing into the default mailbox (RFC 5228 4.3); Exim reports it as fileinto `inbox'.
    # No action and no implicit keep means the message is dropped: the same outcome as discard (RFC 5228 4.4).
    if isinstance(res.get("intent"), list):
        norm = {"keep" if x.lower() == "fileinto:inbox" else
                ("redirect:" + x.split(":", 1)[1].strip().strip("<>") if x.startswith("redirect:") else x)
                for x in res["intent"]}
        res["intent"] = sorted(norm) or ["discard"]
    print(json.dumps(res, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
