#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

usage() {
    echo "Usage: $0 <environment>"
    echo ""
    echo "Arguments:"
    echo "  environment    Environment name (e.g., baremetal)"
    echo ""
    echo "Example:"
    echo "  $0 baremetal"
    exit 1
}

if [ -z "$1" ]; then
    echo "Error: Environment name is required"
    usage
fi

ENV_NAME="$1"
ENV_DIR="$SCRIPT_DIR/$ENV_NAME"
KUBECONFIG_FILE="$ROOT_DIR/.kube/${ENV_NAME}.config"

if [ ! -d "$ENV_DIR" ]; then
    echo "Error: Environment directory not found: $ENV_DIR"
    exit 1
fi

if [ ! -f "$KUBECONFIG_FILE" ]; then
    echo "Error: Kubeconfig not found: $KUBECONFIG_FILE"
    exit 1
fi

export KUBECONFIG="$KUBECONFIG_FILE"

echo "Deploying platform components for environment: $ENV_NAME"
echo "Using kubeconfig: $KUBECONFIG_FILE"
echo ""

cd "$ENV_DIR"

# Deploy Helm releases
if [ -f "helmfile.yaml" ]; then
    echo "=== Deploying Helm releases ==="
    helmfile sync
    echo ""
fi

# Deploy Kustomize resources
if [ -f "kustomization.yaml" ]; then
    echo "=== Deploying Kustomize resources ==="
    kubectl kustomize . | kubectl apply -f -
    echo ""
fi

echo "All platform components deployed successfully!"
