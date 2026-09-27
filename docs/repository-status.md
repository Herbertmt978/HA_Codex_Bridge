# Repository status

The composer reads branch, working-tree changes and an explicitly selected branch comparison base from Git. Refresh repository rereads external edits; changing the comparison scope/reference refreshes its base context. Changed files are shown with staged/working-tree status, with a maximum of 200 entries and an explicit truncation message.

Workspace is the approved chat folder. Repository is the Git root relative to that workspace, currently `.`. Each request is bound to the selected chat and workspace; identical branch names do not identify a repository. Only Git metadata at the exact approved workspace root is supported. No ancestor or recursive repository discovery takes place, and external Git metadata, linked worktrees and unsafe metadata remain unsupported. Non-Git folders and unavailable Git state have separate messages. Status requests never write the source repository.

The panel and Integration require `git_context_v1`. Late responses from another chat selection or comparison base are discarded, including returning to the same chat. An explicit refresh supersedes a pending request. This source implementation does not establish native Home Assistant acceptance.
