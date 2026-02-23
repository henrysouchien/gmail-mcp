# gmail-mcp

MCP server for Gmail operations.

## Tools

| Tool | Description |
|------|-------------|
| `gmail_list_labels` | List system and user Gmail labels |
| `gmail_list_inbox` | List messages from a label (optionally unread only) |
| `gmail_search_emails` | Search emails with Gmail query syntax |
| `gmail_read_email` | Read a full email by message ID |
| `gmail_send_email` | Send a new email |
| `gmail_reply_email` | Reply to a message/thread (supports reply-all) |
| `gmail_manage_labels` | Add or remove labels on a message |
| `gmail_delete_email` | Move message to trash or permanently delete |

## Setup

### Prerequisites
- Python 3.10+
- Gmail account with API access
- OAuth desktop client credentials from Google Cloud Console

### Installation
```bash
git clone https://github.com/<your-user>/gmail-mcp.git
cd gmail-mcp
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

### Authentication
1. Save OAuth credentials as `gmail_credentials.json` in the repository root.
2. Run initial authentication once:
```bash
source venv/bin/activate
python -c "from src import gmail_client; gmail_client.authenticate()"
```
3. Complete browser consent. A local `gmail_token.pickle` cache will be created.

### Claude Code Configuration
Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "gmail-mcp": {
      "type": "stdio",
      "command": "/path/to/gmail-mcp/venv/bin/python",
      "args": ["/path/to/gmail-mcp/run_server.py"]
    }
  }
}
```

## Development

```bash
source venv/bin/activate
python -c "from src.server import mcp; print([t.name for t in mcp._tool_manager._tools.values()])"
python run_server.py
```

## License
MIT
