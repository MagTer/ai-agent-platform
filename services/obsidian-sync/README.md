# Obsidian LiveSync Setup Guide

This guide helps you migrate from Obsidian Sync to self-hosted CouchDB + LiveSync plugin, enabling the AI agent to access your vault.

## Overview

**What you'll get:**
- Self-hosted vault sync via CouchDB (no Obsidian Sync subscription needed)
- Sync across all devices (desktop, mobile, web)
- AI agent can read/write notes in your vault via MCP integration
- Full data ownership and control

**Migration effort:** ~30-60 minutes one-time setup

---

## Prerequisites

- Docker and Docker Compose installed
- Domain name with DNS configured (e.g., `agent.example.com`)
- Traefik reverse proxy running (from main AI agent platform stack)
- Obsidian installed on devices you want to sync

---

## Step 1: Start CouchDB Service

### 1.1 Configure Environment

```bash
cd services/obsidian-sync
cp .env.template .env
```

Edit `.env`:
```bash
COUCHDB_USER=admin
COUCHDB_PASSWORD=<generate-with-openssl-rand-hex-32>
DOMAIN=agent.example.com  # Your domain
```

Generate password:
```bash
openssl rand -hex 32
```

### 1.2 Create External Network

The `proxy` network allows CouchDB to be discovered by Traefik (which runs in the main agent stack).

```bash
docker network create proxy
```

### 1.3 Start CouchDB

```bash
docker compose up -d
```

Verify it's running:
```bash
docker compose ps
docker compose logs -f couchdb
```

Check health:
```bash
curl https://couchdb.agent.example.com/
# Should return: {"couchdb":"Welcome","version":"3.x.x"}
```

### 1.4 Create Database

```bash
# Replace with your domain and credentials
curl -X PUT https://admin:<password>@couchdb.agent.example.com/obsidian
```

Expected response:
```json
{"ok":true}
```

---

## Step 2: Install LiveSync Plugin

### On Desktop (First Device)

1. Open Obsidian
2. Go to Settings → Community Plugins
3. Disable "Safe Mode" if needed
4. Browse Community Plugins
5. Search for "Self-hosted LiveSync"
6. Install and Enable

### Configure LiveSync

1. Go to Settings → Self-hosted LiveSync
2. Click "Setup Wizard"
3. Choose "Use existing remote database"
4. Enter connection details:
   - **Database URI:** `https://couchdb.agent.example.com/obsidian`
   - **Username:** `admin`
   - **Password:** `<your-couchdb-password>`
   - **Database name:** `obsidian`
5. Test connection (should show green checkmark)
6. Click "Next"
7. Configure sync settings:
   - **Sync Mode:** Periodic with real-time (recommended)
   - **Periodic sync interval:** 60 seconds
   - **Batch size:** 50
   - **Enable real-time sync:** Yes
8. Click "Apply and Start Sync"

### Initial Sync

LiveSync will upload your vault to CouchDB. This may take several minutes depending on vault size.

Monitor progress in LiveSync settings (shows sync status and file count).

---

## Step 3: Add Additional Devices

### Desktop/Laptop

Repeat Step 2 on each device. On the second+ device:
1. Install LiveSync plugin
2. Run Setup Wizard with **same credentials**
3. LiveSync will download existing vault from CouchDB

**Important:** Wait for full sync to complete before editing notes.

### Mobile (Android/iOS)

1. Install Obsidian app
2. Create a new empty vault OR open existing vault
3. Install Self-hosted LiveSync plugin (same steps as desktop)
4. Configure with **same credentials**
5. Wait for initial sync

**Tip:** On mobile, enable "Sync on mobile network" in LiveSync settings if needed.

---

## Step 4: Verify Sync

1. Create a test note on Device A: `test-sync.md`
2. Wait ~60 seconds (or trigger manual sync in LiveSync settings)
3. Check Device B - the note should appear

**If sync fails:**
- Check CouchDB logs: `docker compose logs -f couchdb`
- Verify credentials match on all devices
- Check LiveSync status in plugin settings (red = error, green = synced)
- Review LiveSync logs in Obsidian settings → Self-hosted LiveSync → Log

---

## Step 5: Disable Obsidian Sync (If Migrating)

**IMPORTANT:** Only do this AFTER confirming LiveSync works on all devices.

1. On each device:
   - Go to Settings → Sync (Obsidian's official sync)
   - Click "Disconnect" or "Stop syncing this vault"
2. Cancel Obsidian Sync subscription (if desired)

---

## Step 6: Configure Agent Access

The AI agent accesses your vault via the `vault-mcp` service.

### 6.1 Verify vault-mcp is Running

The `vault-mcp` service should already be running in your main AI agent stack.

Check status:
```bash
cd ../../  # Back to project root
docker compose ps vault-mcp
```

### 6.2 Register MCP Server in Admin Portal

1. Go to `https://agent.example.com/platformadmin/mcp/`
2. Click "Add MCP Server"
3. Fill in details:
   - **Name:** `obsidian-vault`
   - **URL:** `http://vault-mcp:8090`
   - **Transport:** `streamable_http`
   - **Auth:** `none` (internal network)
4. Click "Test Connection" (should discover 4 tools)
5. Save

### 6.3 Test Agent Access

Send a message to your agent:
```
Search my vault for notes about "project ideas"
```

The agent will use the `vault_search` tool to find matching notes.

---

## Vault Directory Convention

The agent has **read-only access** to your entire vault, but can **only write** to the `_ai-platform/` directory.

Recommended structure:
```
Vault root/
  _ai-platform/          # Agent write-allowed zone
    memory/              # Agent's persistent memory
    skills/              # Per-context agent skills
    heartbeat.md         # Proactive task list (future)
  Projects/              # Your PARA (read-only for agent)
  Areas/
  Resources/
  Archive/
```

Create the directory:
1. In Obsidian, create a folder named `_ai-platform`
2. Add a `.gitkeep` or `README.md` file so it syncs

The agent will automatically create files under `_ai-platform/` as needed.

---

## Maintenance

### Database Compaction

CouchDB databases can grow over time due to revision history. Compact periodically:

```bash
curl -X POST https://admin:<password>@couchdb.agent.example.com/obsidian/_compact
```

Add this as a cron job (monthly):
```bash
0 0 1 * * curl -X POST https://admin:$COUCHDB_PASSWORD@couchdb.agent.example.com/obsidian/_compact
```

### Backup

CouchDB data is stored in Docker volume `obsidian-sync_couchdb_data`.

Backup:
```bash
docker run --rm \
  -v obsidian-sync_couchdb_data:/data \
  -v $(pwd)/backups:/backup \
  alpine tar czf /backup/couchdb-$(date +%Y%m%d).tar.gz /data
```

Restore:
```bash
docker run --rm \
  -v obsidian-sync_couchdb_data:/data \
  -v $(pwd)/backups:/backup \
  alpine sh -c "cd /data && tar xzf /backup/couchdb-YYYYMMDD.tar.gz --strip-components=1"
```

### Monitoring

Check CouchDB status:
```bash
curl https://admin:<password>@couchdb.agent.example.com/_utils/
```

Check sync logs (on any device):
- Obsidian Settings → Self-hosted LiveSync → Log

---

## Troubleshooting

### Sync Conflicts

LiveSync uses last-write-wins by default. If conflicts occur:
1. Go to LiveSync settings
2. Click "Show Conflicts"
3. Resolve manually (pick a version or merge)

### Connection Errors

**"Failed to connect to CouchDB"**
- Verify CouchDB is running: `docker compose ps`
- Check Traefik routing: `curl https://couchdb.agent.example.com/`
- Verify credentials in `.env` match LiveSync settings

**"SSL Certificate Error"**
- Ensure Let's Encrypt cert is valid: `curl -v https://couchdb.agent.example.com/`
- Wait a few minutes for cert to provision (first startup)

### Slow Sync

- Reduce batch size in LiveSync settings (try 25 instead of 50)
- Increase sync interval (try 120 seconds)
- Check CouchDB resource limits in `docker-compose.yml` (may need more memory for large vaults)

### Database Corruption

If CouchDB becomes corrupted:
1. Stop CouchDB: `docker compose down`
2. Remove volume: `docker volume rm obsidian-sync_couchdb_data`
3. Restore from backup OR re-sync from a device
4. Start CouchDB: `docker compose up -d`

---

## Security Considerations

**Network Access:**
- CouchDB is only accessible via HTTPS (Traefik enforces TLS)
- No direct port exposure (5984 is internal only)
- Basic auth required for all operations

**Agent Write Restrictions:**
- Agent can ONLY write to `_ai-platform/` prefix (enforced by vault-mcp server)
- All other directories are read-only for the agent
- User can review/edit agent-written notes in Obsidian

**Credentials:**
- CouchDB password should be strong (32+ chars)
- Store credentials in `.env` (never commit to git)
- Rotate password periodically (update on all devices + vault-mcp)

---

## Alternative: Manual Testing (Without LiveSync)

To test vault-mcp without setting up LiveSync:

1. Access CouchDB admin UI:
   ```
   https://couchdb.agent.example.com/_utils/
   ```

2. Create database `obsidian` (if not exists)

3. Create a test document:
   ```bash
   curl -X PUT https://admin:<password>@couchdb.agent.example.com/obsidian/test.md \
     -H "Content-Type: application/json" \
     -d '{
       "_id": "test.md",
       "data": "# Test Note\n\nThis is a test.",
       "type": "plain",
       "ctime": 1707000000000,
       "mtime": 1707000000000,
       "size": 30
     }'
   ```

4. Test vault-mcp tools via agent:
   ```
   Search my vault for "test"
   Read note at path test.md
   ```

---

## Resources

- [Self-hosted LiveSync Plugin](https://github.com/vrtmrz/obsidian-livesync)
- [CouchDB Documentation](https://docs.couchdb.org/)
- [Obsidian Plugin Development](https://docs.obsidian.md/)

---

## Support

If you encounter issues:
1. Check CouchDB logs: `docker compose logs -f couchdb`
2. Check LiveSync logs in Obsidian settings
3. Verify Traefik routing: `docker logs traefik 2>&1 | grep couchdb`
4. Check vault-mcp logs: `docker compose -f ../../docker-compose.yml logs vault-mcp`
