# Local MCP implementation notes

The private relay keeps local network permission separate from workspace and
host access. The UI asks for an endpoint-specific acknowledgement and keeps the
custom MCP flow alongside the HA-MCP guide.

Native qualification was necessary: the mocked configuration layer had hidden
both default-field expansion and recursive table merging. Those observations
now have regression coverage. Future runtime upgrades should repeat native
restart and disabled-state checks as well as fresh connection tests.

Authentication expansion and stdio support remain separate roadmap items.
