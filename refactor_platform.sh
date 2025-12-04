#!/bin/bash
set -e

# 1. Create services directory
echo "Creating services directory..."
mkdir -p services

# 2. Clean up unwanted directories
echo "Removing rich/ and typer/ directories..."
rm -rf rich typer

# 3. Move Service Folders
echo "Moving service folders..."
for service in embedder fetcher indexer ragproxy; do
    if [ -d "$service" ]; then
        echo "Moving $service to services/..."
        mv "$service" services/
    fi
done

# 4. Move Config Folders
echo "Moving config folders..."
for config in litellm qdrant openwebui searxng; do
    if [ -d "$config" ]; then
        echo "Moving $config to services/..."
        mv "$config" services/
    fi
done

# 5. Create Agent Service
echo "Setting up services/agent..."
mkdir -p services/agent

if [ -d "src" ]; then
    echo "Moving src/ to services/agent/src/..."
    mv src services/agent/
fi

if [ -f "Dockerfile.agent" ]; then
    echo "Moving and renaming Dockerfile.agent..."
    mv Dockerfile.agent services/agent/Dockerfile
fi

if [ -f "pyproject.toml" ]; then
    echo "Moving pyproject.toml..."
    mv pyproject.toml services/agent/
fi

if [ -f "poetry.lock" ]; then
    echo "Moving poetry.lock..."
    mv poetry.lock services/agent/
fi

# Move misc python files to agent to clean root
for file in conftest.py test_skill_loader.py stack_up.log fix_migration.py; do
    if [ -f "$file" ]; then
        echo "Moving $file to services/agent/..."
        mv "$file" services/agent/
    fi
done

# 6. Update docker-compose.yml
echo "Updating docker-compose.yml paths..."

# Backup
cp docker-compose.yml docker-compose.yml.bak

# Update Agent
sed -i 's|context: .|context: ./services/agent|' docker-compose.yml
sed -i 's|dockerfile: Dockerfile.agent|dockerfile: Dockerfile|' docker-compose.yml

# Update paths for other services
# Note: The 'context: ./qdrant' becomes 'context: ./services/qdrant'
sed -i 's|context: ./qdrant|context: ./services/qdrant|' docker-compose.yml
sed -i 's|context: ./embedder|context: ./services/embedder|' docker-compose.yml
sed -i 's|context: ./ragproxy|context: ./services/ragproxy|' docker-compose.yml
sed -i 's|context: ./fetcher|context: ./services/fetcher|' docker-compose.yml

# Update volumes
sed -i 's|\./litellm/config.yaml|\./services/litellm/config.yaml|' docker-compose.yml
sed -i 's|\./searxng/settings.yml|\./services/searxng/settings.yml|' docker-compose.yml
sed -i 's|\./openwebui/data|\./services/openwebui/data|' docker-compose.yml

echo "Refactoring complete. Check docker-compose.yml and the services/ directory."
