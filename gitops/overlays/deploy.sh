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

# Check if aws-credentials secret exists in argocd namespace
if ! kubectl get secret aws-credentials -n argocd &>/dev/null; then
    echo "Error: Secret 'aws-credentials' not found in namespace 'argocd'"
    echo ""
    echo "Please create the secret first:"
    echo "  kubectl create namespace argocd"
    echo "  kubectl create secret generic aws-credentials -n argocd \\"
    echo "    --from-literal=access-key=<ACCESS_KEY_ID> \\"
    echo "    --from-literal=secret-access-key=<SECRET_ACCESS_KEY>"
    exit 1
fi

echo "Deploying GitOps components for environment: $ENV_NAME"
echo "Using kubeconfig: $KUBECONFIG_FILE"
echo ""

cd "$ENV_DIR"

# Deploy bootstrap resources
echo "=== Deploying bootstrap/argocd ==="
kubectl kustomize bootstrap/argocd | kubectl apply -f -
echo ""

echo "=== Deploying bootstrap/external-secrets-resources ==="
kubectl kustomize bootstrap/external-secrets-resources | kubectl apply -f -
echo ""

# Deploy root ApplicationSet
echo "=== Deploying root-appset.yaml ==="
kubectl apply -f root-appset.yaml
echo ""

echo "GitOps bootstrap completed!"
echo "ArgoCD will now manage the remaining applications."
