#!/bin/bash
# Quickstart script for lmcache-redis-enhanced

set -e

echo "================================================"
echo "  LMCache Redis Enhanced - Quick Start"
echo "================================================"
echo

# Check Docker
echo "Checking Docker..."
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Please install Docker first."
    exit 1
fi

if ! docker ps &> /dev/null; then
    echo "❌ Docker daemon not running. Please start Docker."
    exit 1
fi
echo "✓ Docker is running"
echo

# Check Python
echo "Checking Python..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.10+."
    exit 1
fi
PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
echo "✓ Python $PYTHON_VERSION found"
echo

# Install dependencies
echo "Installing Python dependencies..."
python3 -m pip install -r requirements.txt -q
echo "✓ Dependencies installed"
echo

# Start Redis
echo "Starting Redis (standalone + cluster)..."
docker-compose up -d
echo "✓ Redis containers started"
echo

# Wait for cluster
echo "Waiting for cluster to initialize (this may take 10-15 seconds)..."
sleep 10

# Check cluster status
MAX_RETRIES=10
RETRY=0
while [ $RETRY -lt $MAX_RETRIES ]; do
    if docker logs redis-cluster-init 2>&1 | grep -q "OK.*All 16384 slots covered"; then
        echo "✓ Cluster initialized successfully"
        break
    fi
    RETRY=$((RETRY+1))
    if [ $RETRY -eq $MAX_RETRIES ]; then
        echo "⚠️  Cluster initialization timeout. Check logs:"
        echo "   docker logs redis-cluster-init"
        exit 1
    fi
    sleep 2
done
echo

# Verify setup
echo "Verifying setup..."
python3 verify_setup.py

echo
echo "================================================"
echo "  Setup Complete!"
echo "================================================"
echo
echo "Next steps:"
echo "  1. Run tests:       pytest tests/ -v"
echo "  2. Run benchmarks:  python bench/test_redis_connector.py"
echo "  3. View docs:       cat README.md"
echo
echo "To stop Redis:"
echo "  docker-compose down"
echo
