# App-owned browser worker: blocked acceptance

## Decision

The App image contains the fixed Chromium worker boundary and pinned Chromium
`150.0.7871.124-r0`, but the `ha_browser` capability is deliberately **not
advertised**.  A missing browser-worker attestation is a hard not-ready state;
the Bridge must not start the helper or fall back to a parent-network browser.

## Built-image evidence

The staged amd64 App image built successfully from the pinned Home Assistant
Alpine 3.24 base and reported Chromium `150.0.7871.124`.

The required isolation proof failed in that built image:

- root and `codexbridge` both received a kernel denial when Bubblewrap tried
  to create user and network namespaces;
- a non-root Chromium launch with a private `0700` profile, real HOME/XDG
  paths, and no sandbox-bypass flag aborted because Chromium could not move
  into its namespace.

The current parent AppArmor profile is also not a browser boundary: it permits
the service profile to open `inet`/`inet6` sockets and read App-private `/data`
and workspace paths. A Chromium child inherits that authority. Proxy flags,
RLIMITs, a private profile directory, CDP pipe transport, URL validation, and
the high-level tool schema are useful defence in depth, but none can prove that
a compromised renderer cannot read the Bridge token or open a direct socket.
For that reason no process writes the browser-worker attestation and production
startup does not construct or advertise `browser_v1`.

This is a capability proof failure, not a reason to grant extra privileges.
The release must not add `SYS_ADMIN`, host networking, or `--no-sandbox` to
make this appear to work.

## Safe next route

When the target HAOS runtime permits a separately attested Bubblewrap user and
network namespace, the worker design is:

1. the parent process runs the signed connection-time `BrowserPolicyProxy`;
2. a filesystem Unix-domain socket bridges that parent proxy into the browser
   namespace;
3. an in-namespace, loopback-only TCP relay is the only endpoint Chromium can
   reach; Chromium is forced to use it and has QUIC/WebRTC disabled;
4. Chromium remains headless with a private CDP pipe, an ephemeral profile,
   bounded actions/time/bytes, and no browser-visible port; and
5. hostile public/private/redirect/rebinding/subresource tests prove that
   direct network access and reads of App-private token/workspace paths fail
   while an allowed public page succeeds through the policy proxy.

The root-side proof must inspect the launched worker/Chromium PID's effective
LSM profile, mount visibility, namespace, and socket behavior. A static JSON
claim or the mere presence of a pinned package is not sufficient.

That route requires fresh built-image and target-HA proof before an attestation
is created or a capability is negotiated.  It is not accepted by the package
pin or the worker/client unit tests alone.
