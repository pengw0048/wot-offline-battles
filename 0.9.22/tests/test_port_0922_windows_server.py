from pathlib import Path
import unittest


PORT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PORT_ROOT.parent
RUST_ROOT = PORT_ROOT / 'rust_server'
SERVER_ROOT = PORT_ROOT / 'server'



class RustWindowsServerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_source = (
            RUST_ROOT / 'src' / 'config.rs'
        ).read_text(encoding='utf-8')
        cls.firewall_source = (
            RUST_ROOT / 'src' / 'windows_firewall.rs'
        ).read_text(encoding='utf-8')
        cls.main_source = (
            RUST_ROOT / 'src' / 'main.rs'
        ).read_text(encoding='utf-8')
        cls.build_source = (
            SERVER_ROOT / 'build_windows_server.ps1'
        ).read_text(encoding='utf-8')
        cls.readme = (
            SERVER_ROOT / 'WINDOWS_SERVER_README.txt'
        ).read_text(encoding='utf-8')
        cls.acceptance_source = (
            PORT_ROOT / 'tools' / 'windows_migration_acceptance.ps1'
        ).read_text(encoding='utf-8')
        cls.acceptance_readme = (
            PORT_ROOT / 'tools' / 'WINDOWS_MIGRATION_ACCEPTANCE.md'
        ).read_text(encoding='utf-8')
        cls.workflow = (
            REPOSITORY_ROOT / '.github' / 'workflows' / 'tests.yml'
        ).read_text(encoding='utf-8')
        cls.windows_server_job = cls.workflow.split(
            '\n  windows-server:', 1
        )[1].split('\n  windows-launcher:', 1)[0]

    def test_retired_python_server_sources_are_absent(self):
        for name in (
                'descriptor_projection.py', 'lan_battle_server.py',
                'offline_rewards.py', 'server_battle_authority.py',
                'server_bot_ai.py', 'server_world.py',
                'vehicle_overlay_store.py', 'windows_server.py'):
            self.assertFalse((SERVER_ROOT / name).exists(), name)
        for name in (
                'build_windows_server.ps1', 'WINDOWS_SERVER_README.txt'):
            self.assertTrue((SERVER_ROOT / name).is_file(), name)

    def test_firewall_check_precedes_direct_server_bind(self):
        firewall = 'match ensure_for_bind(&config.host, config.port)?'
        bind = 'ServerApp::bind(config)?'

        self.assertIn(firewall, self.main_source)
        self.assertIn(bind, self.main_source)
        self.assertLess(
            self.main_source.index(firewall),
            self.main_source.index(bind),
        )

    def test_rule_is_exact_executable_and_tcp_28782(self):
        self.assertIn(
            'pub const DEFAULT_PORT: u16 = 28_782;',
            self.config_source,
        )
        for required in (
                'std::env::current_exe()',
                'firewall_rule_name(executable_text, port)',
                'format!("program={executable}")',
                '"protocol=TCP".to_owned()',
                'format!("localport={port}")',
                'format!("remoteip={FIREWALL_REMOTE_IP}")',
                'Get-NetFirewallApplicationFilter',
                '$_.Program -eq {executable}',
                'Get-NetFirewallPortFilter',
                "$_.Protocol -eq 'TCP'",
                "LocalPort -eq '{port}'",
                'WoT 0.9.22 LAN Server TCP 28782 - 318c4e959012'):
            self.assertIn(required, self.firewall_source)

    def test_firewall_tools_use_the_trusted_windows_system_directory(self):
        for required in (
                'GetSystemDirectoryW',
                'trusted_system_path('
                'r"WindowsPowerShell\\v1.0\\powershell.exe")',
                'trusted_system_path("netsh.exe")',
                'Command::new(powershell)',
                'elevated_add_script(netsh, arguments)',
                'Start-Process -FilePath',
                '-Verb RunAs -WindowStyle Normal -Wait -PassThru'):
            self.assertIn(required, self.firewall_source)

        self.assertNotIn('Command::new("powershell', self.firewall_source)
        self.assertNotIn('Command::new("netsh', self.firewall_source)

    def test_loopback_skips_platform_work_and_uac_cancel_is_nonfatal(self):
        ensure = self.firewall_source.index('pub fn ensure_for_bind')
        loopback = self.firewall_source.index(
            'if host_is_loopback(host)', ensure)
        windows = self.firewall_source.index('#[cfg(windows)]', loopback)

        self.assertLess(loopback, windows)
        self.assertIn(
            'return Ok(FirewallOutcome::SkippedLoopback);',
            self.firewall_source,
        )
        self.assertIn('address.is_loopback()', self.firewall_source)
        self.assertIn(
            'const ELEVATION_CANCELLED_EXIT: i32 = 5;',
            self.firewall_source,
        )
        self.assertIn(
            'Some(ELEVATION_CANCELLED_EXIT)',
            self.firewall_source,
        )
        self.assertIn(
            'Ok(ElevationRequest::Cancelled)',
            self.firewall_source,
        )
        self.assertIn(
            'Err(FirewallError::ElevationExit(other))',
            self.firewall_source,
        )
        self.assertIn(
            'ElevationRequest::Completed => match query_existing_rule',
            self.firewall_source,
        )
        self.assertIn(
            'FirewallOutcome::RequestCancelled => eprintln!(',
            self.main_source,
        )

    def test_other_startup_errors_are_user_visible_and_fatal(self):
        self.assertIn(
            'match ensure_for_bind(&config.host, config.port)?',
            self.main_source,
        )
        self.assertIn('eprintln!("error: {error}");', self.main_source)
        self.assertIn('ExitCode::from(2)', self.main_source)
        for required in (
                'QuerySpawn',
                'QueryWait',
                'QueryTimeout',
                'QueryExit',
                'RuleMismatch',
                'ElevationSpawn',
                'ElevationWait',
                'ElevationTimeout',
                'ElevationExit',
                'RuleCreationMissing'):
            self.assertIn(required, self.firewall_source)

    def test_build_delivers_the_direct_x64_rust_executable(self):
        for required in (
                '$RustRoot = Join-Path $PortRoot "rust_server"',
                '$Target = "x86_64-pc-windows-msvc"',
                'offline-rust-server.exe',
                'cargo build `',
                '--manifest-path (Join-Path $RustRoot "Cargo.toml")',
                '--locked `',
                '--release `',
                '--target $Target',
                'Copy-Item -LiteralPath $BuiltExe '
                '-Destination $PackagedExe -Force',
                '$ExpectedFiles = @("README.txt", '
                '"WoT-0.9.22-LAN-Server.exe")'):
            self.assertIn(required, self.build_source)

        self.assertNotIn('PyInstaller', self.build_source)
        self.assertNotIn('windows_server.py', self.build_source)

    def test_windows_workflow_gates_the_rust_firewall_rule(self):
        job = self.windows_server_job
        for required in (
                'rustup target add x86_64-pc-windows-msvc',
                'Build Rust x64 LAN server',
                '$serverExe = Join-Path $serverRoot '
                '"WoT-0.9.22-LAN-Server.exe"',
                '$identity = "$normalizedExe|28782|any"',
                'WoT 0.9.22 LAN Server TCP 28782 - $digest',
                'Start-Process `\n              -FilePath $serverExe',
                'Get-NetFirewallRule',
                '$_.Direction -eq "Inbound"',
                'Get-NetFirewallApplicationFilter',
                '$application.Program -ne $serverExe',
                'Get-NetFirewallPortFilter',
                '$portFilter.Protocol -ne "TCP"',
                '$portFilter.LocalPort -ne "28782"',
                'Rust server did not create its firewall rule',
                'Remove-NetFirewallRule'):
            self.assertIn(required, job)

        self.assertNotIn('PyInstaller', job)
        self.assertNotIn('windows_server.py', job)
        self.assertNotIn('requirements-windows-build.txt', job)
        self.assertIn(
            'cargo test --manifest-path 0.9.22/rust_server/Cargo.toml',
            self.workflow,
        )
        self.assertIn('--all-targets --locked', self.workflow)
        self.assertIn(
            "assert 'he_explosion_evidence_v1' in "
            "reply['server_capabilities'], reply",
            self.workflow,
        )

    def test_workflow_here_strings_stay_at_the_powershell_baseline(self):
        self.assertIn('python-version: "3.11.9"', self.windows_server_job)
        self.assertIn(
            "\n          $ProtocolProbe = @'\n          import json\n",
            self.windows_server_job,
        )
        self.assertIn(
            "\n          '@\n\n          $process",
            self.windows_server_job,
        )
        self.assertNotIn("\n              @'\n", self.windows_server_job)

    def test_windows_readme_describes_the_rust_firewall_contract(self):
        for required in (
                'x64 Rust LAN server',
                'exact executable and TCP 28782 before binding',
                'Cancelling is nonfatal',
                'Loopback-only single player does not request a firewall rule',
                'GNU GPL',
                'Cargo.lock',
                'THIRD_PARTY_NOTICES.md'):
            self.assertIn(required, self.readme)

    def test_migration_acceptance_uses_the_full_modern_probe_contract(self):
        for required in (
                'role = "probe"',
                '"projectile_ledger_v2"',
                '"ram_contact_ledger_v3"',
                '"he_explosion_evidence_v1"',
                '$echoedCapabilities.Count -eq $ProbeCapabilities.Count',
                '$serverCapabilities.Count -eq '
                '$RequiredServerCapabilities.Count',
                'foreach ($capability in $ProbeCapabilities)',
                'foreach ($capability in $RequiredServerCapabilities)'):
            self.assertIn(required, self.acceptance_source)
        self.assertNotIn(
            'capabilities = @("projectile_ledger_v1")',
            self.acceptance_source,
        )

    def test_migration_acceptance_requires_direct_and_splash_he_evidence(self):
        for source in (self.acceptance_source, self.acceptance_readme):
            self.assertIn('one HE direct hit', source)
            self.assertIn('one nearby HE splash', source)
            self.assertIn('both visible clients', source)


if __name__ == '__main__':
    unittest.main()
