//! Windows Firewall admission for the packaged LAN server.
//!
//! The check runs before the listening socket is bound. Loopback-only rooms
//! skip it, while non-Windows builds keep the same server startup path without
//! attempting platform commands.

use std::io;

use thiserror::Error;

#[cfg(any(windows, test))]
use sha2::{Digest, Sha256};

#[cfg(windows)]
use std::ffi::OsString;
#[cfg(windows)]
use std::os::windows::ffi::OsStringExt;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
use std::path::{Path, PathBuf};
#[cfg(windows)]
use std::process::{Command, Stdio};
#[cfg(windows)]
use std::thread;
#[cfg(windows)]
use std::time::{Duration, Instant};

#[cfg(any(windows, test))]
const FIREWALL_RULE_PREFIX: &str = "WoT 0.9.22 LAN Server";
#[cfg(any(windows, test))]
const FIREWALL_REMOTE_IP: &str = "any";
#[cfg(windows)]
const FIREWALL_QUERY_TIMEOUT: Duration = Duration::from_secs(60);
#[cfg(windows)]
const FIREWALL_ELEVATION_TIMEOUT: Duration = Duration::from_secs(60);
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
#[cfg(any(windows, test))]
const ELEVATION_CANCELLED_EXIT: i32 = 5;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FirewallOutcome {
    SkippedLoopback,
    NotWindows,
    AlreadyPresent,
    RuleCreated,
    RequestCancelled,
}

#[derive(Debug, Error)]
pub enum FirewallError {
    #[error("could not determine the Windows LAN server executable path: {0}")]
    CurrentExecutable(#[source] io::Error),
    #[error("the Windows LAN server executable path is not valid Unicode")]
    NonUnicodeExecutable,
    #[error("could not resolve the trusted Windows system directory: {0}")]
    SystemDirectory(#[source] io::Error),
    #[error("could not start the trusted Windows Firewall query: {0}")]
    QuerySpawn(#[source] io::Error),
    #[error("the Windows Firewall query failed: {0}")]
    QueryWait(#[source] io::Error),
    #[error("the Windows Firewall query timed out after 60 seconds")]
    QueryTimeout,
    #[error("the Windows Firewall query exited unexpectedly with code {0:?}")]
    QueryExit(Option<i32>),
    #[error("an existing Windows Firewall rule has the server identity but not its exact executable and TCP port")]
    RuleMismatch,
    #[error("could not start the elevated Windows Firewall request: {0}")]
    ElevationSpawn(#[source] io::Error),
    #[error("could not wait for the elevated Windows Firewall request: {0}")]
    ElevationWait(#[source] io::Error),
    #[error("the elevated Windows Firewall request timed out after 60 seconds")]
    ElevationTimeout,
    #[error("the elevated Windows Firewall request failed with code {0:?}")]
    ElevationExit(Option<i32>),
    #[error(
        "the elevated Windows Firewall request returned success without creating its exact rule"
    )]
    RuleCreationMissing,
}

/// Ensure a non-loopback Windows bind has a narrowly scoped inbound rule.
///
/// A cancelled UAC prompt is returned as a nonfatal outcome. Every other
/// platform failure is returned to the caller before it opens the listener.
pub fn ensure_for_bind(host: &str, port: u16) -> Result<FirewallOutcome, FirewallError> {
    if host_is_loopback(host) {
        return Ok(FirewallOutcome::SkippedLoopback);
    }

    #[cfg(not(windows))]
    {
        let _ = port;
        Ok(FirewallOutcome::NotWindows)
    }

    #[cfg(windows)]
    {
        ensure_windows_rule(port)
    }
}

fn host_is_loopback(host: &str) -> bool {
    let value = host.trim();
    if value.eq_ignore_ascii_case("localhost") {
        return true;
    }
    let unbracketed = value
        .strip_prefix('[')
        .and_then(|value| value.strip_suffix(']'))
        .unwrap_or(value);
    unbracketed
        .parse::<std::net::IpAddr>()
        .is_ok_and(|address| address.is_loopback())
}

#[cfg(windows)]
fn ensure_windows_rule(port: u16) -> Result<FirewallOutcome, FirewallError> {
    let executable = std::env::current_exe().map_err(FirewallError::CurrentExecutable)?;
    let executable_text = executable
        .to_str()
        .ok_or(FirewallError::NonUnicodeExecutable)?;
    let rule_name = firewall_rule_name(executable_text, port);
    let powershell = trusted_system_path(r"WindowsPowerShell\v1.0\powershell.exe")?;
    let script = firewall_query_script(&rule_name, executable_text, port);

    match query_existing_rule(&powershell, &script)? {
        RuleQuery::Exists => return Ok(FirewallOutcome::AlreadyPresent),
        RuleQuery::Mismatch => return Err(FirewallError::RuleMismatch),
        RuleQuery::Missing => {}
    }

    println!("Windows Firewall access needs approval for LAN clients; opening one UAC prompt.");
    let netsh = trusted_system_path("netsh.exe")?;
    let arguments = firewall_add_arguments(&rule_name, executable_text, port);
    match request_elevated_rule(&powershell, &netsh, &arguments)? {
        ElevationRequest::Cancelled => Ok(FirewallOutcome::RequestCancelled),
        ElevationRequest::Completed => match query_existing_rule(&powershell, &script)? {
            RuleQuery::Exists => Ok(FirewallOutcome::RuleCreated),
            RuleQuery::Mismatch => Err(FirewallError::RuleMismatch),
            RuleQuery::Missing => Err(FirewallError::RuleCreationMissing),
        },
    }
}

#[cfg(any(windows, test))]
fn firewall_rule_name(executable: &str, port: u16) -> String {
    let normalized_path = executable.replace('/', "\\").to_lowercase();
    let identity = format!(
        "{}|{}|{}",
        normalized_path,
        port,
        FIREWALL_REMOTE_IP.to_lowercase()
    );
    let digest = Sha256::digest(identity.as_bytes());
    let suffix = digest[..6]
        .iter()
        .map(|value| format!("{value:02x}"))
        .collect::<String>();
    format!("{FIREWALL_RULE_PREFIX} TCP {port} - {suffix}")
}

#[cfg(any(windows, test))]
fn powershell_single_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

#[cfg(any(windows, test))]
fn firewall_query_script(rule_name: &str, executable: &str, port: u16) -> String {
    let rule_name = powershell_single_quote(rule_name);
    let executable = powershell_single_quote(executable);
    format!(
        "$rules = @(Get-NetFirewallRule -DisplayName {rule_name} -ErrorAction SilentlyContinue); \
         if ($rules.Count -eq 0) {{ exit 1 }}; \
         $rule = $rules | Where-Object {{ \
         $_.Direction -eq 'Inbound' -and $_.Enabled -eq 'True' -and \
         $_.Action -eq 'Allow' }} | Select-Object -First 1; \
         if ($null -eq $rule) {{ exit 2 }}; \
         $application = $rule | Get-NetFirewallApplicationFilter | \
         Where-Object {{ $_.Program -eq {executable} }} | Select-Object -First 1; \
         $portFilter = $rule | Get-NetFirewallPortFilter | \
         Where-Object {{ $_.Protocol -eq 'TCP' -and \
         [string]$_.LocalPort -eq '{port}' }} | Select-Object -First 1; \
         if ($null -eq $application -or $null -eq $portFilter) {{ exit 2 }}; exit 0"
    )
}

#[cfg(any(windows, test))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RuleQuery {
    Exists,
    Missing,
    Mismatch,
}

#[cfg(any(windows, test))]
fn classify_query_exit(code: Option<i32>) -> Result<RuleQuery, FirewallError> {
    match code {
        Some(0) => Ok(RuleQuery::Exists),
        Some(1) => Ok(RuleQuery::Missing),
        Some(2) => Ok(RuleQuery::Mismatch),
        other => Err(FirewallError::QueryExit(other)),
    }
}

#[cfg(windows)]
fn query_existing_rule(powershell: &Path, script: &str) -> Result<RuleQuery, FirewallError> {
    let mut child = Command::new(powershell)
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(FirewallError::QuerySpawn)?;
    let deadline = Instant::now() + FIREWALL_QUERY_TIMEOUT;
    loop {
        match child.try_wait().map_err(FirewallError::QueryWait)? {
            Some(status) => return classify_query_exit(status.code()),
            None if Instant::now() < deadline => {
                thread::sleep(Duration::from_millis(25));
            }
            None => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(FirewallError::QueryTimeout);
            }
        }
    }
}

#[cfg(any(windows, test))]
fn firewall_add_arguments(rule_name: &str, executable: &str, port: u16) -> Vec<String> {
    [
        "advfirewall".to_owned(),
        "firewall".to_owned(),
        "add".to_owned(),
        "rule".to_owned(),
        format!("name={rule_name}"),
        "dir=in".to_owned(),
        "action=allow".to_owned(),
        "enable=yes".to_owned(),
        "profile=any".to_owned(),
        format!("program={executable}"),
        "protocol=TCP".to_owned(),
        format!("localport={port}"),
        format!("remoteip={FIREWALL_REMOTE_IP}"),
    ]
    .into_iter()
    .collect()
}

#[cfg(any(windows, test))]
fn windows_command_line(arguments: &[String]) -> String {
    arguments
        .iter()
        .map(|argument| quote_windows_argument(argument))
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(any(windows, test))]
fn quote_windows_argument(argument: &str) -> String {
    if !argument.is_empty()
        && !argument
            .chars()
            .any(|value| value.is_whitespace() || value == '"')
    {
        return argument.to_owned();
    }

    let mut quoted = String::from("\"");
    let mut backslashes = 0usize;
    for value in argument.chars() {
        if value == '\\' {
            backslashes += 1;
        } else if value == '"' {
            quoted.push_str(&"\\".repeat(backslashes * 2 + 1));
            quoted.push('"');
            backslashes = 0;
        } else {
            quoted.push_str(&"\\".repeat(backslashes));
            backslashes = 0;
            quoted.push(value);
        }
    }
    quoted.push_str(&"\\".repeat(backslashes * 2));
    quoted.push('"');
    quoted
}

#[cfg(any(windows, test))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ElevationRequest {
    Completed,
    Cancelled,
}

#[cfg(any(windows, test))]
fn classify_elevation_exit(code: Option<i32>) -> Result<ElevationRequest, FirewallError> {
    match code {
        Some(0) => Ok(ElevationRequest::Completed),
        Some(ELEVATION_CANCELLED_EXIT) => Ok(ElevationRequest::Cancelled),
        other => Err(FirewallError::ElevationExit(other)),
    }
}

#[cfg(any(windows, test))]
fn elevated_add_script(netsh: &str, arguments: &[String]) -> String {
    let netsh = powershell_single_quote(netsh);
    let command_line = powershell_single_quote(&windows_command_line(arguments));
    format!(
        "try {{ \
         $process = Start-Process -FilePath {netsh} -ArgumentList {command_line} \
         -Verb RunAs -WindowStyle Normal -Wait -PassThru -ErrorAction Stop; \
         if ($process.ExitCode -ne 0) {{ exit 6 }}; exit 0 \
         }} catch {{ \
         $native = $_.Exception; \
         while ($null -ne $native -and \
         -not ($native -is [System.ComponentModel.Win32Exception])) {{ \
         $native = $native.InnerException }}; \
         if ($null -ne $native -and \
         ($native.NativeErrorCode -eq 1223 -or $native.NativeErrorCode -eq 5)) {{ exit 5 }}; \
         exit 7 }}"
    )
}

#[cfg(windows)]
fn request_elevated_rule(
    powershell: &Path,
    netsh: &Path,
    arguments: &[String],
) -> Result<ElevationRequest, FirewallError> {
    let netsh = netsh.to_str().ok_or(FirewallError::NonUnicodeExecutable)?;
    let script = elevated_add_script(netsh, arguments);
    let mut child = Command::new(powershell)
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            &script,
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(FirewallError::ElevationSpawn)?;
    let deadline = Instant::now() + FIREWALL_ELEVATION_TIMEOUT;
    loop {
        match child.try_wait().map_err(FirewallError::ElevationWait)? {
            Some(status) => return classify_elevation_exit(status.code()),
            None if Instant::now() < deadline => thread::sleep(Duration::from_millis(25)),
            None => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(FirewallError::ElevationTimeout);
            }
        }
    }
}

#[cfg(windows)]
fn trusted_system_path(relative: &str) -> Result<PathBuf, FirewallError> {
    const MAX_PATH_CHARS: usize = 32_768;
    let mut buffer = vec![0u16; MAX_PATH_CHARS];
    // SAFETY: the writable buffer has exactly the capacity passed to the API.
    let length = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) };
    if length == 0 {
        return Err(FirewallError::SystemDirectory(io::Error::last_os_error()));
    }
    if length as usize >= buffer.len() {
        return Err(FirewallError::SystemDirectory(io::Error::new(
            io::ErrorKind::InvalidData,
            "GetSystemDirectoryW returned an oversized path",
        )));
    }
    buffer.truncate(length as usize);
    let mut path = PathBuf::from(OsString::from_wide(&buffer));
    path.push(relative);
    Ok(path)
}

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetSystemDirectoryW(buffer: *mut u16, size: u32) -> u32;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loopback_hosts_skip_before_platform_work() {
        for host in ["127.0.0.1", "127.4.3.2", "::1", "[::1]", "LOCALHOST"] {
            assert_eq!(
                ensure_for_bind(host, 28_782).unwrap(),
                FirewallOutcome::SkippedLoopback
            );
        }
        assert!(!host_is_loopback("0.0.0.0"));
        assert!(!host_is_loopback("192.168.1.20"));
    }

    #[cfg(not(windows))]
    #[test]
    fn non_windows_non_loopback_bind_is_unchanged() {
        assert_eq!(
            ensure_for_bind("0.0.0.0", 28_782).unwrap(),
            FirewallOutcome::NotWindows
        );
    }

    #[test]
    fn rule_identity_is_stable_across_windows_path_case_and_slashes() {
        let first = firewall_rule_name(r"C:\Games\WoT\server.exe", 28_782);
        let second = firewall_rule_name(r"c:/games/wot/SERVER.EXE", 28_782);
        assert_eq!(first, second);
        assert_eq!(first, "WoT 0.9.22 LAN Server TCP 28782 - 318c4e959012");
        let suffix = first.rsplit_once(" - ").unwrap().1;
        assert_eq!(suffix.len(), 12);
        assert!(suffix.chars().all(|value| value.is_ascii_hexdigit()));
    }

    #[test]
    fn query_checks_direction_state_action_program_and_exact_tcp_port() {
        let script = firewall_query_script(
            "WoT 0.9.22 LAN Server TCP 28782 - test",
            r"C:\Games\WoT LAN\WoT-0.9.22-LAN-Server.exe",
            28_782,
        );
        for required in [
            "$_.Direction -eq 'Inbound'",
            "$_.Enabled -eq 'True'",
            "$_.Action -eq 'Allow'",
            "Get-NetFirewallApplicationFilter",
            "C:\\Games\\WoT LAN\\WoT-0.9.22-LAN-Server.exe",
            "Get-NetFirewallPortFilter",
            "$_.Protocol -eq 'TCP'",
            "LocalPort -eq '28782'",
        ] {
            assert!(script.contains(required), "missing {required:?}");
        }
    }

    #[test]
    fn elevated_request_is_exact_and_windows_quoted() {
        let arguments = firewall_add_arguments(
            "WoT 0.9.22 LAN Server TCP 28782 - test",
            r"C:\Games\WoT LAN\WoT-0.9.22-LAN-Server.exe",
            28_782,
        );
        let command_line = windows_command_line(&arguments);
        for required in [
            "dir=in",
            "action=allow",
            "enable=yes",
            "profile=any",
            "protocol=TCP",
            "localport=28782",
            "remoteip=any",
        ] {
            assert!(command_line.contains(required));
        }
        assert!(command_line.contains(r#""program=C:\Games\WoT LAN\WoT-0.9.22-LAN-Server.exe""#));
        assert!(command_line.contains(r#""name=WoT 0.9.22 LAN Server TCP 28782 - test""#));
    }

    #[test]
    fn uac_cancellation_is_nonfatal_and_other_elevation_errors_fail() {
        assert_eq!(
            classify_elevation_exit(Some(0)).unwrap(),
            ElevationRequest::Completed
        );
        assert_eq!(
            classify_elevation_exit(Some(5)).unwrap(),
            ElevationRequest::Cancelled
        );
        assert!(matches!(
            classify_elevation_exit(Some(2)),
            Err(FirewallError::ElevationExit(Some(2)))
        ));
    }

    #[test]
    fn elevated_request_waits_for_netsh_and_maps_uac_cancellation() {
        let script = elevated_add_script(
            r"C:\Windows\System32\netsh.exe",
            &firewall_add_arguments(
                "WoT 0.9.22 LAN Server TCP 28782 - test",
                r"C:\Games\WoT LAN\WoT-0.9.22-LAN-Server.exe",
                28_782,
            ),
        );
        for required in [
            "Start-Process",
            "-Verb RunAs",
            "-Wait",
            "-PassThru",
            "$process.ExitCode -ne 0",
            "$native.NativeErrorCode -eq 1223",
            "exit 5",
        ] {
            assert!(script.contains(required), "missing {required:?}");
        }
    }

    #[test]
    fn query_exit_codes_distinguish_missing_mismatch_and_failure() {
        assert_eq!(classify_query_exit(Some(0)).unwrap(), RuleQuery::Exists);
        assert_eq!(classify_query_exit(Some(1)).unwrap(), RuleQuery::Missing);
        assert_eq!(classify_query_exit(Some(2)).unwrap(), RuleQuery::Mismatch);
        assert!(matches!(
            classify_query_exit(Some(9)),
            Err(FirewallError::QueryExit(Some(9)))
        ));
        assert!(matches!(
            classify_query_exit(None),
            Err(FirewallError::QueryExit(None))
        ));
    }

    #[test]
    fn powershell_literals_escape_single_quotes() {
        assert_eq!(powershell_single_quote("a'b"), "'a''b'");
    }

    #[test]
    fn windows_argument_quoting_doubles_trailing_backslashes() {
        assert_eq!(quote_windows_argument(""), "\"\"");
        assert_eq!(
            quote_windows_argument("C:\\Program Files\\"),
            "\"C:\\Program Files\\\\\""
        );
        assert_eq!(quote_windows_argument("plain"), "plain");
    }
}
