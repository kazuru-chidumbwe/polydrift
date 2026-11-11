//! sieve-rs intent runner for the polydrift Sieve cell. Usage: pd-sieve-rs SCRIPT MESSAGE ENV_FROM ENV_TO
//! Prints one JSON line: compile (ok | refused, with the compiler's message) and intent, the actions the
//! interpreter asks its host to perform. The event names are read from their Debug form so the same source builds
//! against releases whose Event enum differs (0.7 to 1.0). Host answers are the lab's: a mailbox exists only if
//! PD_FOLDERS (comma list) names it, no list or duplicate matches, no include, no external command.
use sieve::{Compiler, Envelope, Input, Runtime};
use std::collections::HashMap;

fn esc(s: &str) -> String {
    s.chars()
        .flat_map(|c| match c {
            '"' => "\\\"".chars().collect::<Vec<_>>(),
            '\\' => "\\\\".chars().collect(),
            '\n' => "\\n".chars().collect(),
            c if (c as u32) < 0x20 => vec![' '],
            c => vec![c],
        })
        .collect()
}

fn field(dbg: &str, name: &str) -> Option<String> {
    let k = format!("{name}: \"");
    let i = dbg.find(&k)? + k.len();
    let j = dbg[i..].find('"')?;
    Some(dbg[i..i + j].to_string())
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let script = std::fs::read(&a[1]).unwrap();
    let raw = std::fs::read(&a[2]).unwrap();
    let folders: Vec<String> = std::env::var("PD_FOLDERS")
        .unwrap_or_else(|_| "INBOX".into())
        .split(',')
        .map(|s| s.to_string())
        .collect();
    let compiled = match Compiler::new().compile(&script) {
        Ok(s) => s,
        Err(e) => {
            println!(
                "{{\"engine_version\":\"sieve-rs {}\",\"compile\":\"refused\",\"compile_text\":\"{}\"}}",
                env!("PD_SIEVE_RS_VERSION"),
                esc(&format!("{e:?}"))
            );
            return;
        }
    };
    let runtime = Runtime::new();
    let mut inst = runtime.filter(&raw);
    inst.set_envelope(Envelope::From, a[3].as_str());
    inst.set_envelope(Envelope::To, a[4].as_str());
    let mut input = Input::script("main", compiled);
    let mut intent: Vec<String> = vec![];
    let mut created: HashMap<String, bool> = HashMap::new(); // message_id -> auto-replied
    let mut err: Option<String> = None;
    while let Some(r) = inst.run(input) {
        input = true.into();
        match r {
            Ok(ev) => {
                let d = format!("{ev:?}");
                let name = d.split(|c: char| c == ' ' || c == '{' || c == '(').next().unwrap_or("").to_string();
                match name.as_str() {
                    "MailboxExists" => {
                        let ok = folders.iter().any(|f| d.contains(&format!("\"{f}\"")));
                        input = ok.into();
                    }
                    "ListContains" | "DuplicateId" | "Execute" => input = false.into(),
                    "IncludeScript" => input = false.into(),
                    "Keep" => intent.push("keep".into()),
                    "Discard" => intent.push("discard".into()),
                    "Reject" => intent.push("reject".into()),
                    "FileInto" => intent.push(format!("fileinto:{}", field(&d, "folder").unwrap_or("?".into()))),
                    "CreatedMessage" => {
                        let id = d.split("message_id: ").nth(1).and_then(|s| s.split(',').next()).unwrap_or("").trim().to_string();
                        created.insert(id, d.to_ascii_lowercase().contains("auto-submitted: auto-replied"));
                    }
                    "SendMessage" => {
                        let id = d.split("message_id: ").nth(1).and_then(|s| s.split([',', ' ', '}']).next()).unwrap_or("").trim().to_string();
                        let to = field(&d, "Address").or_else(|| field(&d, "recipient")).unwrap_or_else(|| {
                            d.split('"').nth(1).unwrap_or("?").to_string()
                        });
                        if *created.get(&id).unwrap_or(&false) {
                            intent.push("vacation".into());
                        } else {
                            intent.push(format!("redirect:{to}"));
                        }
                    }
                    "Notify" => intent.push("notify".into()),
                    _ => {}
                }
            }
            Err(e) => {
                err = Some(format!("{e:?}"));
                break;
            }
        }
    }
    intent.sort();
    intent.dedup();
    let items: Vec<String> = intent.iter().map(|s| format!("\"{}\"", esc(s))).collect();
    println!(
        "{{\"engine_version\":\"sieve-rs {}\",\"compile\":\"ok\",\"intent\":[{}],\"runtime_error\":{}}}",
        env!("PD_SIEVE_RS_VERSION"),
        items.join(","),
        err.map(|e| format!("\"{}\"", esc(&e))).unwrap_or("null".into())
    );
}
