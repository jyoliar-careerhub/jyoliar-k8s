#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

usage() {
    echo "Usage: $0 <environment>"
    echo ""
    echo "Arguments:"
    echo "  environment    Environment name (e.g., aws-ec2)"
    echo ""
    echo "Example:"
    echo "  $0 aws-ec2"
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

deploy_kustomize() {
    local dir="$1"
    local name="$(basename "$dir")"

    echo "[$name] Deploying with Kustomize..."
    kubectl kustomize "$dir" | kubectl apply -f -
    echo "[$name] Done"
    echo ""
}

deploy_helm() {
    local dir="$1"
    local name="$(basename "$dir")"

    echo "[$name] Deploying with Helm..."

    cd "$dir"
    helm dependency update
    helm upgrade --install "$name" . --wait
    cd - > /dev/null

    echo "[$name] Done"
    echo ""
}

for component_dir in "$ENV_DIR"/*/; do
    [ -d "$component_dir" ] || continue

    component_name="$(basename "$component_dir")"

    if [ -f "$component_dir/Chart.yaml" ]; then
        deploy_helm "$component_dir"
    elif [ -f "$component_dir/kustomization.yaml" ]; then
        deploy_kustomize "$component_dir"
    else
        echo "[$component_name] Skipping: No Chart.yaml or kustomization.yaml found"
        echo ""
    fi
done

echo "All platform components deployed successfully!"
