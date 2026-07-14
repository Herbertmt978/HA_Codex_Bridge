# Third-party notices

Home Assistant Codex Bridge is licensed under the [MIT License](LICENSE). It
uses and distributes components separately licensed by their authors. This
notice identifies material third-party components; it does not replace license
texts included with an upstream distribution.

| Component | Use in this project | Source / version reference |
| --- | --- | --- |
| Home Assistant | Hosts the Integration and, when available, the Supervisor App. | [Home Assistant](https://www.home-assistant.io/) |
| OpenAI Codex | Runtime used by the private App. | The App release lock pins `openai/codex` `0.144.4`; see [`codex_bridge_app/codex-release.json`](codex_bridge_app/codex-release.json). Codex source is licensed under Apache-2.0; retain its applicable notice and license with any redistribution. |
| Bubblewrap | Tool-process sandbox component bundled from the locked Codex release. | The same release lock identifies the exact asset and verification metadata. |
| Python packages | Run the Bridge service and tests. | Declared in [`bridge_service/pyproject.toml`](bridge_service/pyproject.toml) and generated App requirements files. |
| Home Assistant base image | Base image for the App build. | Declared in [`codex_bridge_app/Dockerfile`](codex_bridge_app/Dockerfile). |

References to Home Assistant, HACS, OpenAI, or other products identify
interoperable components only; they do not imply endorsement, sponsorship, or
affiliation. For an App image, consult the notices and licenses accompanying the
exact upstream binary and container-image distributions used to build it.

If an attribution or license notice is missing, open a non-sensitive issue or
pull request.
