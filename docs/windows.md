# Running on Windows

The harness runs natively in PowerShell; WSL is only used internally by `podman machine`.
Start the Podman machine first (`podman machine start`), then:

```powershell
uv sync
ollama pull qwen3.8:27b
git clone https://github.com/cosmicpython/code code; git -C code checkout 14c84797ffa77255d53cf1a02fe6aafda2b68aeb
podman build --network host -f sandbox/cosmic-python.Containerfile -t harness-cosmic-python code
Copy-Item harness.example.toml harness.toml
```

`--network host` is needed because the Podman machine cannot set up build networking
(netavark/nftables error). It applies to the build only; checks still run with `--network none`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `uv` not found after `pip install --user uv` | Add `%APPDATA%\Python\Python313\Scripts` to `PATH`, or `winget install astral-sh.uv` |
| `ollama ps`: "timed out waiting for server to start"; `server.log`: `bind: ... forbidden` | Hyper-V/WSL reserved a port range containing 11434 (`netsh int ipv4 show excludedportrange protocol=tcp`). In an admin terminal: `net stop winnat`, `netsh int ipv4 add excludedportrange protocol=tcp startport=11434 numberofports=1`, `net start winnat`; then restart Ollama |
| `invalid peer certificate: UnknownIssuer` (uv) or `unable to get local issuer certificate` (git) | Antivirus or a proxy re-signs HTTPS. Use the Windows certificate store: `uv --system-certs ...` and `git config --global http.sslBackend schannel` |

## What the harness does differently on Windows

- The workspace is cloned with `core.autocrlf=false`. Git for Windows checks out CRLF by
  default, and the model's LF edits would then never match.
  (`tests/test_workspace.py::test_clone_keeps_lf_line_endings_even_with_autocrlf`)
- Windows has no process groups to signal, so a timed-out command's process tree is killed with
  `taskkill /F /T`. A child that outlives its parent is no longer part of that tree and is not
  killed. Containers are still removed with `podman rm --force` on timeout.
- Volume sources are passed as forward-slash paths (`C:/...:/work:ro`), which Podman and Docker
  accept.

Tested on one machine: Windows 11 with Podman (WSL machine) and Ollama.
