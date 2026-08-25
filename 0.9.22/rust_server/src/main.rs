use offline_rust_server::clock::FixedStepClock;
use offline_rust_server::config::ServerConfig;
use offline_rust_server::server::ServerApp;
use offline_rust_server::trace::{validate_reader, TraceSummary};
use offline_rust_server::windows_firewall::{ensure_for_bind, FirewallOutcome};
use std::env;
use std::error::Error;
use std::fs::File;
use std::io::{self, BufReader};
use std::process::ExitCode;

const HELP: &str = "\
Offline Battles Rust LAN server

Usage:
  offline-rust-server [serve] [server options]
  offline-rust-server clock-probe [ticks]
  offline-rust-server validate-stream [path|-]

Commands:
  serve            Run the protocol-v5 LAN server (default).
  clock-probe      Emit JSON observations from the monotonic 30 Hz clock.
  validate-stream  Validate a JSONL native-oracle shadow trace (stdin by default).

Server options:
  --host HOST              Listen address (default: 0.0.0.0).
  --port PORT              TCP port (default: 28782).
  --map MAP                Initial map (default: server_random).
  --max-players COUNT      Room capacity (default: 30).
  --team-size COUNT        Capacity of each team (default: 15).
  --team1-size COUNT       Team 1 capacity.
  --team2-size COUNT       Team 2 capacity.
  --receipt-state PATH     Durable receipt state path.
";

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::from(2)
        }
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let all_args: Vec<String> = env::args().skip(1).collect();
    let Some(command) = all_args.first().map(String::as_str) else {
        return run_server(Vec::new());
    };

    match command {
        "serve" => {
            if all_args
                .get(1)
                .is_some_and(|value| value == "--help" || value == "-h")
            {
                print!("{HELP}");
            } else {
                run_server(all_args.into_iter().skip(1).collect())?;
            }
        }
        "clock-probe" => {
            let mut args = all_args.into_iter().skip(1);
            let ticks = args
                .next()
                .map(|value| value.parse::<u64>())
                .transpose()?
                .unwrap_or(10);
            ensure_no_extra_args(args)?;
            run_clock_probe(ticks)?;
        }
        "validate-stream" => {
            let mut args = all_args.into_iter().skip(1);
            let path = args.next().unwrap_or_else(|| "-".to_owned());
            ensure_no_extra_args(args)?;
            let summary = if path == "-" {
                let stdin = io::stdin();
                validate_reader(stdin.lock())?
            } else {
                validate_reader(BufReader::new(File::open(path)?))?
            };
            print_summary(&summary)?;
        }
        "help" | "--help" | "-h" => print!("{HELP}"),
        value if value.starts_with("--") => run_server(all_args)?,
        _ => return Err(format!("unknown command {command:?}\n\n{HELP}").into()),
    }
    Ok(())
}

fn run_server(args: Vec<String>) -> Result<(), Box<dyn Error>> {
    let config = ServerConfig::from_process(args)?;
    match ensure_for_bind(&config.host, config.port)? {
        FirewallOutcome::RuleCreated => println!(
            "Windows Firewall rule verified for TCP {} (all remote addresses).",
            config.port
        ),
        FirewallOutcome::RequestCancelled => eprintln!(
            "Windows Firewall access was not approved; remote LAN clients may remain blocked."
        ),
        FirewallOutcome::SkippedLoopback
        | FirewallOutcome::NotWindows
        | FirewallOutcome::AlreadyPresent => {}
    }
    let mut server = ServerApp::bind(config)?;
    println!("LAN battle server listening on {}", server.local_addr());
    server.run()?;
    Ok(())
}

fn ensure_no_extra_args(mut args: impl Iterator<Item = String>) -> Result<(), Box<dyn Error>> {
    if let Some(extra) = args.next() {
        return Err(format!("unexpected argument {extra:?}").into());
    }
    Ok(())
}

fn run_clock_probe(ticks: u64) -> Result<(), Box<dyn Error>> {
    let mut clock = FixedStepClock::new();
    for _ in 0..ticks {
        println!("{}", serde_json::to_string(&clock.wait_next()?)?);
    }
    Ok(())
}

fn print_summary(summary: &TraceSummary) -> Result<(), serde_json::Error> {
    println!("{}", serde_json::to_string(summary)?);
    Ok(())
}
