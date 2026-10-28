#!/bin/bash

set -e

usage() {
    echo "Usage: $0 -f <credentials-json-path> -k <kubeconfig-path>"
    echo ""
    echo "Options:"
    echo "  -f    Cloudflare Tunnel credentials JSON (terraform output -raw tunnel_credentials)"
    echo "  -k    Path to kubeconfig file"
    echo "  -h    Show this help message"
    exit 1
}

while getopts "f:k:h" opt; do
    case $opt in
        f) CREDENTIALS_PATH="$OPTARG" ;;
        k) KUBECONFIG_PATH="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [[ -z "$CREDENTIALS_PATH" || -z "$KUBECONFIG_PATH" ]]; then
    echo "Error: All arguments are required."
    usage
fi

if [[ ! -f "$CREDENTIALS_PATH" ]]; then
    echo "Error: credentials file not found: $CREDENTIALS_PATH"
    exit 1
fi

if [[ ! -f "$KUBECONFIG_PATH" ]]; then
    echo "Error: kubeconfig file not found: $KUBECONFIG_PATH"
    exit 1
fi

TUNNEL_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["TunnelID"])' "$CREDENTIALS_PATH")
if [[ -z "$TUNNEL_ID" ]]; then
    echo "Error: TunnelID not found in credentials file: $CREDENTIALS_PATH"
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
    --from-file=credentials.json="$CREDENTIALS_PATH" \
    --from-literal=tunnel-id="$TUNNEL_ID" \
    --kubeconfig "$KUBECONFIG_PATH" \
    --dry-run=client -o yaml | \
    kubectl apply --kubeconfig "$KUBECONFIG_PATH" -f -

echo "Secret '$SECRET_NAME' created/updated in namespace '$NAMESPACE'"
