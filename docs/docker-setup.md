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
