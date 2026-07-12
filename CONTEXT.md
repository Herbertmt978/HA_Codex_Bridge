# Project Context: Home Assistant Codex Bridge

## Language

| Term | Definition | Avoid |
|------|------------|-------|
| **App** | A Supervisor-managed package that runs alongside Home Assistant and hosts supporting software. | “add-on” except when quoting older Home Assistant material; “integration” |
| **Integration** | The Home Assistant component that provides setup, access control, and the Codex Bridge panel inside Home Assistant. | “app”; “add-on”; “bridge service” |
| **Bridge** | The local service that accepts authorised Home Assistant requests and coordinates Codex work. | “Windows VM”; “integration”; “Codex itself” |
| **Workspace** | A deliberately granted folder in which Codex may inspect and change project files. | “all files”; “Home Assistant config”; “project” |
| **Project** | A user-visible grouping of Codex chats associated with one workspace and a set of defaults. | “workspace”; “repository” |

## Relationships

- An **App** hosts the **Bridge** on a Home Assistant system.
- The **Integration** connects Home Assistant users to the **Bridge**.
- A **Project** selects one **Workspace** for its Codex chats.
- A **Workspace** contains the files Codex is allowed to work with.

## Flagged Ambiguities

- “Home Assistant add-on” → **App**; Home Assistant renamed add-ons to apps, while older documentation and APIs may retain the old wording (2026-07-12).
