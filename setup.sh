#!/bin/sh

# Setup script for Due Diligence Reporter MCP Server
echo "Setting up Due Diligence Reporter MCP Server..." >&2

# Install dependencies using uv.
#
# --frozen: do NOT re-resolve the dependency graph. Use uv.lock as-is.
#   This is the request-path-blocking step on MCP Hive cold starts; without
#   --frozen, every cold container does a full resolver pass on the ~14
#   direct deps (and their transitive graph), which has caused 60s tools/list
#   timeouts on `mcp-server.ti.trilogy.com`. With --frozen this drops to ~2s.
# --no-dev: skip dev/test extras in the runtime container.
#
# `uv sync` also installs the project itself, so the separate
# `uv pip install -e .` that used to run here is no longer needed.
echo "Installing dependencies (frozen, no dev extras)..." >&2
uv sync --frozen --no-dev > /dev/null 2>&1

# Create credentials directory
mkdir -p credentials

# Load .env file into shell environment (vars are in the file but not exported by default)
if [ -f .env ]; then
    echo "Loading .env into shell environment..." >&2
    set -a
    . ./.env
    set +a
fi

# --- OAuth2 token setup ---
# Priority 1: Build token from .env / environment variables (same method as cron workflows)
# This uses the known-good refresh token from GitHub secrets.
if [ -n "$OAUTH_REFRESH_TOKEN" ] && [ -n "$OAUTH_CLIENT_ID" ] && [ -n "$OAUTH_CLIENT_SECRET" ]; then
    echo "Building OAuth2 token from environment variables..." >&2
    python3 -c "
import json, os
data = {
    'token': None,
    'refresh_token': os.environ['OAUTH_REFRESH_TOKEN'],
    'token_uri': 'https://oauth2.googleapis.com/token',
    'client_id': os.environ['OAUTH_CLIENT_ID'],
    'client_secret': os.environ['OAUTH_CLIENT_SECRET'],
    'scopes': [
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/documents',
        'https://www.googleapis.com/auth/gmail.modify',
    ],
}
with open('.gcp-saved-tokens.json', 'w') as f:
    json.dump(data, f, indent=2)

# Also create client_secrets.json for compatibility
secrets = {
    'installed': {
        'client_id': os.environ['OAUTH_CLIENT_ID'],
        'client_secret': os.environ['OAUTH_CLIENT_SECRET'],
        'redirect_uris': ['http://localhost'],
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
    }
}
with open('credentials/client_secrets.json', 'w') as f:
    json.dump(secrets, f, indent=2)
"
    echo "OAuth2 credentials configured from environment variables" >&2

# Priority 2: Fetch from MCP Hive platform (legacy fallback)
elif [ -n "$API_KEY" ] && [ -n "$API_BASE_URL" ] && [ -n "$HIVE_INSTANCE_ID" ]; then
    echo "Fetching OAuth2 configuration from MCP Hive..." >&2

    if curl -s -X GET "$API_BASE_URL/api/hive-instances/$HIVE_INSTANCE_ID/oauth2-config" \
        -H "x-api-key: $API_KEY" > oauth_response.json 2>&1; then

        echo "Configuring OAuth2 credentials from MCP Hive..." >&2

        jq '{
          "client_id": .oauthKeys.client_id,
          "client_secret": .oauthKeys.client_secret,
          "refresh_token": .credentials.refresh_token,
          "token": .credentials.access_token,
          "token_uri": "https://oauth2.googleapis.com/token",
          "type": "authorized_user",
          "scopes": [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/gmail.modify"
          ]
        }' oauth_response.json > .gcp-saved-tokens.json

        jq '{"web": .oauthKeys}' oauth_response.json > credentials/client_secrets.json

        echo "OAuth2 credentials configured from MCP Hive" >&2
        rm oauth_response.json
    else
        echo "OAuth2 configuration fetch failed, will use manual setup" >&2
    fi
else
    echo "No OAuth2 credentials found in environment — using existing token files" >&2
fi

echo "Setup complete!" >&2

# Output final JSON configuration to stdout (MANDATORY)
cat << EOF
{
  "command": "uv",
  "args": ["run", "due-diligence-reporter-mcp"],
  "env": {
    "GOOGLE_CLIENT_CONFIG": "credentials/client_secrets.json",
    "GOOGLE_TOKEN_FILE": ".gcp-saved-tokens.json"
  },
  "cwd": "$(pwd)"
}
EOF
