# exchange-mcp skills

Agent skills for the [`exchange-mcp`](../README.md) MCP server. Each skill teaches an agent how to use one domain of the server's 31 tools: email, calendar, contacts, and mailbox metadata.

Load only the skill(s) relevant to the task — they are independent.

| Skill | Tools | Use when |
|---|---|---|
| [`exchange-email`](exchange-email/SKILL.md) | 15 | Reading, searching, sending, organizing mail; managing folders, drafts, attachments |
| [`exchange-calendar`](exchange-calendar/SKILL.md) | 9 | Listing/creating/updating/deleting events, responding to invites, finding free slots, availability |
| [`exchange-contacts`](exchange-contacts/SKILL.md) | 5 | Searching the GAL and personal contacts, CRUD on personal contacts |
| [`exchange-mailbox`](exchange-mailbox/SKILL.md) | 2 | Health checks and mailbox metadata (size, quota, timezone, server version) |

## Prerequisite

The `exchange` MCP server must already be configured in the host agent's MCP config. See the [repo README](../README.md#configure) for the environment variables (`EXCHANGE_SERVER`, `EXCHANGE_USERNAME`, `EXCHANGE_PASSWORD`, `EXCHANGE_EMAIL_ADDRESS`, `EXCHANGE_AUTH_TYPE`, …).

Skills assume the MCP server is exposed under the name **`exchange`**.

## Install

Pick the host agent below. Install only the domains you want loaded — each is independent.

### opencode

Symlink (recommended, stays in sync with the repo):

```bash
ln -s "$PWD/skills/exchange-email"      ~/.config/opencode/skills/exchange-email
ln -s "$PWD/skills/exchange-calendar"   ~/.config/opencode/skills/exchange-calendar
ln -s "$PWD/skills/exchange-contacts"   ~/.config/opencode/skills/exchange-contacts
ln -s "$PWD/skills/exchange-mailbox"    ~/.config/opencode/skills/exchange-mailbox
```

Or copy if you prefer a frozen snapshot:

```bash
cp -r skills/exchange-* ~/.config/opencode/skills/
```

### Claude Code / GSD

```bash
mkdir -p .claude/skills
ln -s "$PWD/skills/exchange-email"      .claude/skills/exchange-email
ln -s "$PWD/skills/exchange-calendar"   .claude/skills/exchange-calendar
ln -s "$PWD/skills/exchange-contacts"   .claude/skills/exchange-contacts
ln -s "$PWD/skills/exchange-mailbox"    .claude/skills/exchange-mailbox
```

### Generic / other hosts

Each skill is a plain `SKILL.md` with YAML frontmatter (`name`, `description`) followed by markdown. Drop the directory wherever your host loads skills from.

## Format notes

- Frontmatter uses only `name` and `description` — no host-specific `allowed-tools` field, because this MCP is not a bash CLI.
- Bodies are plain markdown (not the pure-XML opencode convention). Both opencode and Claude Code parse markdown skill bodies fine.
- Tool argument schemas are sourced from [`src/exchange_mcp/models.py`](../src/exchange_mcp/models.py); if the server adds a field, update the relevant skill.
