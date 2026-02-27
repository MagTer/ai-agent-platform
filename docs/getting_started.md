# Getting Started

This guide covers setting up the AI Agent Platform. You can run it in a **Local/Development mode** (accessible via `localhost` without SSL) or a **Production mode** (exposed to the internet with a real domain, Traefik reverse proxy, and Let's Encrypt SSL).

## Prerequisites
- Docker & Docker Compose plugin
- Python 3.11+
- Poetry (for managing the `stack` CLI environment)
- OpenSSL (for generating secure secrets)

---

## 1. Initial Setup (Required for all environments)

### Copy the Environment Template
```bash
cp .env.template .env
```

### Generate Cryptographic Secrets
The `.env` file contains several placeholder tags (e.g., `<generate-with-openssl-rand-hex-32>`) that must be replaced with strong, random strings before the stack will run securely.

Run this script in your terminal to automatically generate and inject secure keys into your `.env` file:

```bash
# 1. Generate Postgres Password
sed -i "s/<generate-with-openssl-rand-hex-16>/$(openssl rand -hex 16)/g" .env

# 2. Generate API and Session Secrets
for _ in {1..5}; do
  sed -i "0,/<generate-with-openssl-rand-hex-32>/{s/<generate-with-openssl-rand-hex-32>/$(openssl rand -hex 32)/}" .env
done

# 3. Generate Fernet Encryption Key for User Credentials
FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
sed -i "s/<generate-fernet-key>/$FERNET_KEY/g" .env
```

### Configure Required External APIs
Open your `.env` file and manually configure the following:
- `OPENROUTER_API_KEY`: Required for the LLM to function. Get one at [OpenRouter](https://openrouter.ai/).

### Install the CLI environment
```bash
poetry install
```

---

## 2. Option A: Local Development Deployment

If you just want to test the platform locally on your own machine without a public domain, use the default `stack up` command. This uses `docker-compose.override.yml` to bind ports directly to `localhost`.

### Start the Stack
```bash
./stack up
```

### Access the Platform
- **Chat Interface:** Open [http://localhost:3000](http://localhost:3000)

*Note: In a pure local setup without Microsoft Entra ID configured, you will not be able to access the Admin Portal (`/admin`), as it strictly requires an authenticated `platform-admin` OAuth role.*

---

## 3. Option B: Production Deployment (VPS / Public Domain)

If you are deploying to a server (like an AWS VM or DigitalOcean Droplet), you must use the production profile. This enables Traefik, which handles routing, security headers, and automatically provisions Let's Encrypt SSL certificates.

### Configure Domains in `.env`
1. Point your DNS A-records (e.g., `agent.yourdomain.com`) to your server's IP address.
2. Update your `.env` file with your real domain:
   ```env
   DOMAIN=agent.yourdomain.com
   ACME_EMAIL=your-email@yourdomain.com
   ```

### Start the Production Stack
Use the `prod` command to launch using `docker-compose.prod.yml`:
```bash
./stack prod up -d
```

### Access the Platform
- **Chat Interface:** Open `https://agent.yourdomain.com`
- **Admin Portal:** Open `https://agent.yourdomain.com/platformadmin` (Requires Entra ID setup).

---

## 4. Configuring Microsoft Entra ID (Required for Admin Access)

The platform uses Entra ID (Azure AD) to manage roles. To access the Admin Portal and manage the AI agent's contexts, tools, and credentials, you must configure this integration.

1. Create an App Registration in the [Azure Portal](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps).
2. Add your redirect URIs:
   - Local: `http://localhost:3000/oauth/callback`
   - Prod: `https://agent.yourdomain.com/oauth/callback`
3. Update your `.env`:
   ```env
   MICROSOFT_CLIENT_ID=your_client_id
   MICROSOFT_CLIENT_SECRET=your_client_secret
   MICROSOFT_CLIENT_TENANT_ID=your_tenant_id
   ```
4. Restart the stack (`./stack prod up -d --build`).

*When logging in, ensure your user in Azure AD has the role claim mapping to `platform-admin` to access the Admin dashboard.*

---

## Stack CLI Summary

The `stack` CLI wraps Docker Compose and handles environment parsing. 

| Command | Environment | Description |
|---------|-------------|-------------|
| `./stack up` | Local | Start the local stack (`localhost:3000`). |
| `./stack dev up` | Dev | Start the dev stack behind Traefik (`DOMAIN_DEV`). |
| `./stack prod up` | Prod | Start the prod stack behind Traefik (`DOMAIN`). |
| `./stack status` | All | Show container health checks. |
| `./stack logs [service]`| All | Tail logs for a specific container. |
| `./stack prod down` | Prod | Stop the production stack. |