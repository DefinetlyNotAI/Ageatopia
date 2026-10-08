# Local Docker setup status

Checked on 2026-10-09 on this Windows 11 machine.

- Installed Docker Desktop 4.94.0 in per-user mode with the WSL 2 backend.
- Docker CLI reports version 29.8.2.
- Official installer SHA256 matched the Windows Package Manager manifest:
  `a9814e31049d66156477a86614e83365669677733014ec72f74229623ff3890a`.
- Authenticode status was Valid, signed by Docker Inc.; installer exited 0.
- WSL 2.7.13.0 is present and the Windows hypervisor is running.
- Docker engine activation and the Agent Utopia image build remain blocked.

## Actual startup blocker

The existing user `.wslconfig` selects
`6.18.33.2-MSWSL2-CustomShahmKernal`. Its running kernel identifies itself as
`6.18.33.2-microsoft-standard-WSL2+`. `/proc/filesystems` contains no ISO9660
support, and the Docker distribution has no `/lib/modules` directory to load it
from. Docker's backend log reports:

```
mounting cli-tools iso: ... unknown filesystem type 'iso9660'
```

The custom kernel and `.wslconfig` have been left unchanged. Finishing Docker
setup requires either a Docker-compatible standard WSL kernel or rebuilding the
custom kernel with `CONFIG_ISO9660_FS=y`. Changing the global WSL kernel also
affects the existing Kali distribution. Microsoft's current kernel release notes
[describe the ISO9660 built-in change](https://github.com/microsoft/WSL2-Linux-Kernel/releases).

Docker Desktop opened its first-use subscription agreement screen. No license
acceptance flag was used and the agreement was not accepted on the user's behalf.
Docker requires acceptance before normal use; see its
[Windows installation instructions](https://docs.docker.com/desktop/setup/install/windows-install/).

Once those activation steps are resolved, open a fresh terminal so the installer’s
PATH change is picked up, check `docker info`, then run:

```powershell
docker build -t agent-utopia-world:1 sandbox
$env:UTOPIA_INTEGRATION = '1'
.\.venv\Scripts\python.exe -m pytest -m integration -v
```

The running operator backend was started with the new per-user Docker CLI path.
No host-execution fallback, kernel replacement, WSL shutdown or Kali changes were
performed.
