/** Run the same provider-neutral client contract through three isolated routes. */

import { spawn } from "node:child_process";

const profiles = [
  {
    name: "lan",
    origin: "http://proxy:8080",
    host: "ha-lan.invalid",
    proto: "http",
    cfRay: "",
  },
  {
    name: "nabu-shaped",
    origin: "http://proxy:8082",
    host: "ha-nabu.invalid",
    proto: "https",
    cfRay: "",
  },
  {
    name: "cloudflare-shaped",
    origin: "http://proxy:8083",
    host: "ha-cloudflare.invalid",
    proto: "https",
    cfRay: "synthetic-cloudflare-ray",
  },
];

function runProfile(profile) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["/workspace/e2e.spec.js", "--client"], {
      env: {
        ...process.env,
        PROXY_ORIGIN: profile.origin,
        TRANSPORT_EXPECTED_PROFILE: profile.name,
        TRANSPORT_EXPECTED_HOST: profile.host,
        TRANSPORT_EXPECTED_PROTO: profile.proto,
        TRANSPORT_EXPECTED_CF_RAY: profile.cfRay,
      },
      stdio: "inherit",
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0 && signal === null) {
        resolve();
        return;
      }
      reject(new Error(`transport profile ${profile.name} failed (${code ?? signal})`));
    });
  });
}

for (const profile of profiles) {
  await runProfile(profile);
}

process.stdout.write("all transport route profiles passed\n");
