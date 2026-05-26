# exchange-mcp

MCP server for Microsoft Exchange access via EWS

## Install

From PyPI:

```bash
pip install exchange-mcp
# or
uv add exchange-mcp
```

From source:

```bash
pip install .
# or
uv pip install .
```

## Configure

Set these environment variables:

```bash
export EXCHANGE_SERVER=https://mail.company.com/EWS/Exchange.asmx
export EXCHANGE_USERNAME="DOMAIN\\username"
export EXCHANGE_PASSWORD=secret
export EXCHANGE_EMAIL_ADDRESS=user@company.com
export EXCHANGE_AUTH_TYPE=NTLM
```

## Claude Code

```json
{
  "mcpServers": {
    "exchange": {
      "command": "exchange-mcp",
      "env": {
        "EXCHANGE_SERVER": "https://mail.company.com/EWS/Exchange.asmx",
        "EXCHANGE_USERNAME": "DOMAIN\\\\username",
        "EXCHANGE_PASSWORD": "secret",
        "EXCHANGE_EMAIL_ADDRESS": "user@company.com",
        "EXCHANGE_AUTH_TYPE": "NTLM"
      }
    }
  }
}
```
