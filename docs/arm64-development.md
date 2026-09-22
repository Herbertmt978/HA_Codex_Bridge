# ARM64 development and qualification

ARM64 image development is underway for [issue #111](https://github.com/Herbertmt978/HA_Codex_Bridge/issues/111).
Release 1.3.1 includes the development build preparation described here.
The stable App still supports **amd64 only**. An ARM64 image building or running
under emulation does not establish support for a Raspberry Pi, Green, Yellow
or another ARM64 Home Assistant OS device.

## What is implemented

The context stager selects locked ARM64 Codex, Bubblewrap and code-mode host
binaries and hash-locked ARM64 Python wheels. The image checks the Docker target
against Home Assistant's build architecture and verifies each executable before
installation. Runtime startup checks the actual machine architecture against
the immutable sandbox contract before reporting it to the Integration.

The build workflow checks amd64 and aarch64 images independently. Its release
jobs and Supervisor metadata remain amd64-only until native qualification is
complete. No published image or release tag is replaced by these checks.

| Feature | ARM64 development status |
| --- | --- |
| Core Bridge, Codex and workspace sandbox | Build path implemented; native HAOS acceptance outstanding. The existing sandbox proof remains mandatory. |
| Sign-in, chat, schedules, terminal and MCP | Require the native acceptance tests below. Build/import checks do not establish these features work on HAOS. |
| Interactive browser | Not qualified. ARM64 images omit Chromium, discard stale browser proof and cannot advertise browser tools. Existing native web search is a separate feature. |
| Host Access App | Still amd64-only; separate installation and native qualification required. |
| Minimum RAM, storage and supported boards | Not yet established by native testing. Do not infer limits from an emulated benchmark. |

## Build a development image

Use the Python/uv tooling in [Development](development.md). Keep the generated
context outside source control. Use a separate output directory per architecture:

```sh
python scripts/stage_app_context.py --arch aarch64 --output .build/arm64-context
docker build --platform linux/arm64 --build-arg BUILD_ARCH=aarch64 \
  --tag codex-bridge-arm64:dev .build/arm64-context
```

Home Assistant names this architecture `aarch64`; Docker names its platform
`linux/arm64`. For the amd64 control build, use `--arch amd64`,
`--platform linux/amd64` and `--build-arg BUILD_ARCH=amd64` with a different
context and development tag. A mismatched build argument must fail.

Use emulation for package, ELF, import and basic startup checks. Record the
Docker host architecture as well as the image architecture: `uname -m` inside
an emulated container can report aarch64. Do not disable the workspace sandbox
or mark its proof successful merely to make an emulated chat run.

## Native acceptance before release

On 22 September 2026, both image builds passed, along with ARM64 emulated
executable/import checks and amd64 startup/sandbox/MCP checks on HAOS-DEV.
There is currently no native ARM64 test device available. These results qualify
the build preparation only; the following acceptance work remains outstanding.

Use an isolated ARM64 HAOS installation and its own test account. Record the
board/CPU, RAM, storage medium, HAOS/Core/Supervisor versions, kernel, image
identity, original power state and recovery point. Keep credentials out of the
report. Do not use a production Home Assistant machine as the development target.

1. Install the candidate through Supervisor with its actual AppArmor profile.
   Check discovery, authenticated readiness and architecture reporting.
2. Complete device sign-in and a real chat. Create/read/edit files inside the
   workspace; verify private App and HA paths, direct network access and escape
   attempts remain denied. Run the existing native sandbox proof and check that
   failed proof blocks tools rather than selecting an unsafe execution path.
3. Exercise a scheduled run, cancellation and restart recovery; verify no
   duplicate run or leftover child process. Test terminal input, resize, idle
   cleanup and workspace confinement.
4. Test MCP discovery/tool calls and credential rotation/removal with synthetic
   credentials. Check both disabled and enabled options, then restart the App.
5. Test upgrade/restart and backup restoration without losing chats or grants.
   Record resource use and failures; retain at most three HA backups.
6. Keep browser and Host Access unavailable unless separately qualified on this
   target. Recheck the same core behaviours on amd64.

After these pass, extend the publication jobs and advertised architecture in one
reviewed release. Verify signatures, per-architecture software inventories and
source provenance for both images, and that the generic manifest selects the
correct image on each native host. Until then, leave issue #111 open.

Reference: [Home Assistant build arguments](https://developers.home-assistant.io/docs/apps/configuration/#build-args)
and [Docker multi-platform builds](https://docs.docker.com/build/building/multi-platform/).
