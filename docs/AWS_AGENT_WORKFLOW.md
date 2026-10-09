# AWS Agent Workflow

Use this rule in every coding agent or assistant used by the team.

## AWS tooling handshake

When an issue, bug, architecture decision, or recommendation appears to require
live AWS service context, the agent should pause and ask before using AWS tools:

> "This looks AWS-related. Should I use the configured AWS CLI/MCP with your local AWS profile to inspect the project state and suggest the next step?"

If the team member agrees, the agent should:

1. Use the configured AWS CLI/MCP through that team member's local AWS profile.
2. Verify the active project identity before making recommendations, normally with
   `aws sts get-caller-identity` when using the CLI.
3. Verify the selected Region from AWS Settings, or from `~/.aws/config` if the
   team member cannot confirm it.
4. Inspect only the relevant read-only service state first.
5. Explain any proposed write, deploy, or cleanup action separately before doing it.

## Credential rule

Team members must not share AWS credentials, access keys, or another person's
profile. Each team member signs in locally and the agent uses only that local
profile.

Prefer `aws login` for local CLI credentials when available. It provides
short-term credentials and avoids long-lived access keys.

## Region rule

Create Regional AWS resources only in the project's selected Region. Global
services such as CloudFront may still require global/us-east-1 control-plane
actions, but Lambda, API Gateway, databases, queues, and other Regional resources
must stay in the selected Region.

## Suggested agent setup

Copy the handshake and rules above into each agent's project instruction file,
for example:

- `AGENTS.md` for Codex/OpenCode-style agents.
- `GEMINI.md` for Gemini-style agents.
- `.github/copilot-instructions.md` if the team uses GitHub Copilot Chat.
- Any MCP/tool policy prompt used by the team's local assistant setup.
