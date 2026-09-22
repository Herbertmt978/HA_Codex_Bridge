# ARM64 build preparation evidence — 22 September 2026

Baseline: main release 1.3.0. Source, tests, workflows and runtime contracts were
reviewed before changes. The original checkout's unrelated work was preserved.

| Check | Result |
| --- | --- |
| Locked executable provenance | All six Codex, Bubblewrap and code-mode host assets across amd64/aarch64 passed signature, workflow identity and locked-source verification. |
| Reproducible staging | Both architectures passed binary hashes, ELF identities and target Python native-extension checks. |
| Docker images | Both builds passed. Deliberately mismatched build architecture failed immediately at the architecture guard. |
| Runtime probes | Both images imported the Bridge and runtime dependencies, ran Codex and Bubblewrap version checks, and matched actual machine ABI to the immutable sandbox contract. ARM64 was emulated on an amd64 Docker host. |
| ARM browser guard | Refused browser qualification and removed stale proof before attempting launch. |
| Focused Windows tests | 117 passed, two skipped. |
| Full Linux Integration suite | 351 passed. |
| Full Linux Bridge suite | 1,983 passed, 27 skipped; two dependency deprecation warnings. Skips are not evidence of native ARM64 acceptance. |
| Root restoration | Eight passed separately as root. |
| Static checks | Ruff, release synchronisation, actionlint, zizmor and diff whitespace checks passed; existing zizmor exclusions retained. |
| Native amd64 HAOS-DEV | Candidate started under the existing AppArmor profile, produced valid sandbox proof, reported amd64, required sign-in and rejected missing credentials. Authenticated status/thread/project routes and synthetic MCP credential create/replace/remove passed, including write-only responses and no-store. |

Local logs and executable probe scripts are retained outside Git at
`D:/CodexWork/ha-bridge-arm64-evidence-20260922`. Initial probe harness imports
were corrected to match the actual runtime dependencies; final probes passed.
The mismatched-build log confirms the intentional guard rejection.

DEV's installed App and Integration were not upgraded. Test containers,
volumes, candidate image and transfer archive were removed; the temporary
HTTP transfer server was stopped. DEV retained three backups and 6.3 GiB free.
Both task-started guests were restored stopped. Docker on the workstation was
already running and remains running without active task containers.

## Not established

No native ARM64 HAOS device is available. ARM64 sign-in/chat, workspace security,
scheduling, terminal, MCP end-to-end behaviour, process cleanup, recovery and
hardware resource limits have not been qualified. The native amd64 check did
not sign in or submit a model prompt. There was no frontend change or new
frontend test run. The new GitHub ARM build job has not run remotely.

No ARM image was published. Published-image signatures, inventories, provenance
and multi-architecture manifest selection remain future release gates. Stable
metadata and release workflows continue to advertise and publish amd64 only.
