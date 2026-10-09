# Local Docker and custom kernel setup

Checked on 2026-10-09 on this Windows 11 machine.

- Docker Desktop 4.94.0 is installed for all users at `C:\Program Files\Docker\Docker`, with the WSL 2 backend.
- Docker CLI reports version 29.8.2.
- Official installer SHA256 matched the Windows Package Manager manifest:
  `a9814e31049d66156477a86614e83365669677733014ec72f74229623ff3890a`.
- Authenticode status was Valid, signed by Docker Inc.; installer exited 0.
- WSL 2.7.13.0 is present and the Windows hypervisor is running.
- Docker engine activation and the `agent-utopia-world:1` image build pass.

## Custom kernel preservation and upgrade

The original `.wslconfig` selected `6.18.33.2-MSWSL2-CustomShahmKernal`, whose
internal release name was `6.18.33.2-microsoft-standard-WSL2+`. Its configuration
enabled ISO9660 as a module, but Docker could not access Kali's installed modules.
The original error was:

```
mounting cli-tools iso: ... unknown filesystem type 'iso9660'
```

The active kernel now reports **`6.18.54.1-microsoft-standard-WSL2-CustomShahmKernal`**.
It was built from Microsoft's latest WSL kernel release,
[linux-msft-wsl-6.18.54.1](https://github.com/microsoft/WSL2-Linux-Kernel/releases/tag/linux-msft-wsl-6.18.54.1),
commit `d504d40ab83839cb61942a812a99d183c42227b9`, using the original embedded
configuration rather than the reduced upstream defaults.

- No existing configuration option was removed or disabled. ISO9660 and ACPI
  platform profile moved from modules to built in; LOCALVERSION gained the requested
  suffix. The upgrade added four upstream configuration options.
- The original 31,898-byte `rtw88/rtw8821a_fw.bin` is embedded unchanged: its full
  byte sequence was verified in both the original and new uncompressed kernels.
- The old kernel had 945 loadable modules. The new one has 960. The only missing
  old module paths are the two features now built in.
- `rtw88_8821au`, its Realtek dependencies, `xtrx`, `vhci_hcd`, and `usbip_core`
  load successfully on the active kernel. XTRX was rebuilt and installed with DKMS;
  its original installation and both uninstalled Realtek DKMS source packages remain.
- Physical Wi-Fi monitor-mode and SDR operation were not tested.

Source and the full build configuration remain in Kali at
`/home/kali/WSL2-Linux-Kernel-6.18.54.1-CustomShahmKernal`. Rebuild with
`make -j12 LOCALVERSION= bzImage modules` to retain the exact release suffix.
The active kernel and configuration are also saved in the Windows home directory.
The matching `-modules-wsl2.7.vhdx` uses the flat layout required by WSL 2.7.13;
the newer upstream artifact layout was incompatible with that installed host.

The original kernel is unchanged, with SHA256
`ccc84e77a30b0958008ad6f177fd664aebe2da1dbac22587f4027814582142ef`.
Rollback copies are `C:\Users\Shahm Najeeb\.wslconfig.original-20261009` and
`C:\Users\Shahm Najeeb\6.18.33.2-MSWSL2-CustomShahmKernal.original-20261009`.
To roll back, exit Docker and Kali, restore the saved `.wslconfig`, and run
`wsl --shutdown`. Both original kernel files and original Kali modules remain.

## Docker repair and verification

The per-user installation was completely uninstalled, and the specifically
requested `C:\Users\Shahm Najeeb\AppData\Local\Programs\DockerDesktop` directory
was deleted and verified absent. Its data disk and settings were backed up under
`.data/docker-reinstall-20261009` before removal; the new installation uses fresh data.

The fresh installer omitted the launcher's `HKLM\SOFTWARE\Docker Inc.\Docker Desktop`
key despite registering the uninstall entry successfully. Restoring its string
value `AppPath` to the verified install directory `C:\Program Files\Docker\Docker`
resolved the exact reported launcher error. No license-acceptance flag was used.

`docker info` returns the engine identity. The real sandbox integration passes
permissions, zero agent capabilities, network isolation, shared files, permanent
process termination, CTF execution, observation acknowledgment, and workspace
recovery. It exposed a missing supervisor `FOWNER` capability, now fixed; agent
processes retain zero effective capabilities. The standard suite passes 56 tests.
Real Ollama first-task tool calling passes. The six-agent smoke run passes in
48.53 seconds, completing work and capturing the final workspace. The prompt now
explicitly requests a task before exploring files. Earlier attempts exposed
Windows commit exhaustion and unassigned workspace exploration; clearing build
caches and fixing that initial instruction resolved these observed failures.
The operator backend was restarted with the new CLI path and reports both Docker
and Ollama ready. Open a fresh terminal to pick up the system Docker PATH change.

## Earlier sandbox image scan: 2026-10-09

The first security rebuild used digest-pinned Python 3.14.8 on Alpine 3.24, upgrades installed
OS packages, and installs only the required compiler, process tools, curl, strace,
and setpriv rather than the full util-linux collection. It retains the worker UID
and group 10000. The unused pip installer and its ensurepip bootstrap wheels are
removed entirely, including their vulnerable vendored dependencies. Python and
the C compiler remain available; installing Python packages inside the sandbox is
no longer supported. Host application dependencies are unaffected.

Built image `agent-utopia-world:1`:
`sha256:6e309dfd6100bfa9d587a6d699155ff36fc6ad06092717d647673a8f36f0ef51`.

Two independent scans ran with all severities and unfixed findings included,
without ignore rules, package exclusions, or VEX suppressions:

| Scanner | Original image | Updated image | Database |
| --- | --- | --- | --- |
| Grype 0.120.1 | 1,014 findings | 19 findings: 5 high, 14 medium | Built 2026-10-08 06:33:47 UTC |
| Trivy 0.75.0 | Not scanned | 0 findings | Updated 2026-10-08 19:05:21 UTC |

Counts are package/advisory matches and differ across scanners. Trivy detected
63 Alpine packages and no remaining Python distribution packages; its clean result
does not clear Grype's binary Python/NVD findings. Docker Scout requires a Docker
sign-in on this installation, so the reported original 157 Scout findings and its
updated count could not be independently reproduced. **The requested zero-finding
result across scanners has not been achieved.**

Remaining Grype findings, all retained in the report:

| Package | Findings | Status |
| --- | --- | --- |
| binutils 2.45.1-r1 | CVE-2025-69644, CVE-2025-69647, CVE-2025-69648, CVE-2025-69649, CVE-2025-69650, CVE-2025-69651, CVE-2025-69652, CVE-2026-3441, CVE-2026-3442, CVE-2026-4647, CVE-2026-6844, CVE-2026-6845, CVE-2026-6846 | No fixed version in the scanner or Alpine's security feed; the same version is current even in Alpine edge. Some NVD descriptions explicitly identify disputed pre-release findings. Required by gcc; retained. |
| busybox, busybox-binsh, ssl_client 1.37.0-r31 | CVE-2025-60876, one match per package | No fixed version in the scanner or Alpine's security feed. Core shell/tool implementation retained. |
| Python 3.14.8 | CVE-2025-15367, CVE-2026-12345, CVE-2026-87910 | Grype reports fixes on other branches or Python 3.15, whose stable Docker image is unavailable. The POP3 command and temporary-directory fixes are not present in this interpreter; the 3.14 temporary-directory backport remains open. The Python CNA record excludes 3.14 from CVE-2026-87910, unlike Grype's NVD match. |

Upstream references: [Alpine binutils package](https://pkgs.alpinelinux.org/package/edge/main/x86_64/binutils),
[Alpine security feed](https://secdb.alpinelinux.org/v3.24/main.json),
[Python POP3 advisory](https://mail.python.org/archives/list/security-announce@python.org/thread/CBFBOWVGGUJFSGITQCCBZS4GEYYZ7ZNE/),
[pending Python 3.14 temporary-directory backport](https://github.com/python/cpython/pull/158430),
and [Python tarfile CNA record](https://cveawg.mitre.org/api/cve/CVE-2026-87910).

Local full reports are `.data/image-vulnerabilities-before.json`,
`.data/image-vulnerabilities-after.json`, and `.data/image-trivy-after.json`.
Scanner downloads were verified against their official release SHA256 checksums.
Rebuild and rescan with installed scanners:

```powershell
docker build --pull --no-cache -t agent-utopia-world:1 sandbox
grype docker:agent-utopia-world:1 -o json --file .data/image-vulnerabilities-after.json --fail-on negligible
trivy image --image-src docker --scanners vuln --format json --output .data/image-trivy-after.json --exit-code 1 agent-utopia-world:1
$env:UTOPIA_INTEGRATION = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_integration.py -q
```

All three real integrations pass together in 59.68 seconds on the updated image:
UID/capability and network isolation, shared files, process termination, compiled
CTF execution, observers and recovery; real Ollama tool calling; and six-agent
task completion with final workspace capture. The standard suite passes 56 tests
with the three integrations skipped unless explicitly enabled. Resolving the
remaining findings needs maintained upstream fixes and scanner metadata updates;
removing required tools or hiding the findings would not satisfy the request.


## Current sandbox image: security follow-up

The expected local tag is `agent-utopia-world:1`. The image is built locally;
Docker Hub has no public repository to pull under that name. Rebuild with the
Windows setup command in README.md if this local tag is deleted.

The image uses digest-pinned official Python 3.15.0rc3 on Alpine 3.24. This is a
release candidate, selected because the installed stable branch lacked the POP3
command and temporary-directory security fixes. The interpreter includes control
character rejection and directory-descriptor-based permission cleanup.

Clang/LLVM 22 replaces GNU binutils and supplies `cc`, `gcc`, `ld`, `objdump`,
`strings`, `nm` and `readelf`. Compilation uses compiler-rt and LLD. Trusted
numeric ptrace process events replace strace, including short-lived children and
thread exec ID changes; the observer never decodes mutable tracee filename buffers.

BusyBox 1.38.0 is built from the official checksum-verified source archive with
four published security patches. It is registered as a real APK with its actual
source origin and version. Source compilation dependencies remain in the builder
stage. Agent programs retain the existing unprivileged UID and resource limits.
Python installers remain removed. The kernel and custom drivers are unchanged.

Patch sources and authors:

- Radoslav Kolev: [wget URL validation](https://lists.busybox.net/pipermail/busybox/2025-November/091840.html).
- Sanghyun Park: [AWK replacement storage](https://lists.busybox.net/pipermail/busybox/2026-June/092352.html),
  [shell IFS cleanup](https://lists.busybox.net/pipermail/busybox/2026-June/092360.html),
  and [shell function recursion](https://lists.busybox.net/pipermail/busybox/2026-June/092354.html).

The final unfiltered scans use the same databases recorded above:

| Scanner | Findings | Report |
| --- | --- | --- |
| Grype 0.120.1 | 5 version matches; no ignored findings | `.data/image-vulnerabilities-final.json` |
| Trivy 0.75.0 | 0 | `.data/image-trivy-final.json` |

None of the nine reported binutils/strace CVEs remain. Grype retains
CVE-2026-87910 and CVE-2026-12345 against Python 3.15.0rc3, whose fixes are included
in that release candidate, and CVE-2026-38753/38754/38755 against BusyBox 1.38.0,
whose source patches are included in this build. These NVD version matches do not
account for those fixes. No ignore rules, version relabeling, package exclusions,
or VEX suppression were used. A zero-finding unfiltered Grype result remains
unachieved. Docker Scout still requires account sign-in for an independent scan.

The real integration suite exercises malformed URL rejection, shell recursion
limits, POP3 injection rejection, C compilation, capability/network isolation,
process lifecycles, non-leader thread exec, permanent termination, filesystem
recovery, real Ollama tool calls and all six initial agents acting.
