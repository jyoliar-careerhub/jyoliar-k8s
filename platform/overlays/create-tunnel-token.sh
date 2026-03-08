#!/bin/bash

set -e

usage() {
    echo "Usage: $0 -t <tunnel-token> -k <kubeconfig-path>"
    echo ""
    echo "Options:"
    echo "  -t    Cloudflare Tunnel token"
    echo "  -k    Path to kubeconfig file"
    echo "  -h    Show this help message"
    exit 1
}

while getopts "t:k:h" opt; do
    case $opt in
        t) TUNNEL_TOKEN="$OPTARG" ;;
        k) KUBECONFIG_PATH="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [[ -z "$TUNNEL_TOKEN" || -z "$KUBECONFIG_PATH" ]]; then
    echo "Error: All arguments are required."
    usage
fi

if [[ ! -f "$KUBECONFIG_PATH" ]]; then
    echo "Error: kubeconfig file not found: $KUBECONFIG_PATH"
    exit 1
fi

NAMESPACE="cloudflare-system"
SECRET_NAME="cloudflare-tunnel-token"

# Create namespace if not exists
kubectl create namespace "$NAMESPACE" --kubeconfig "$KUBECONFIG_PATH" --dry-run=client -o yaml | \
    kubectl apply --kubeconfig "$KUBECONFIG_PATH" -f -

# Create or update secret
kubectl create secret generic "$SECRET_NAME" \
    -n "$NAMESPACE" \
    --from-literal=token="$TUNNEL_TOKEN" \
    --kubeconfig "$KUBECONFIG_PATH" \
    --dry-run=client -o yaml | \
    kubectl apply --kubeconfig "$KUBECONFIG_PATH" -f -

echo "Secret '$SECRET_NAME' created/updated in namespace '$NAMESPACE'"
