#!/bin/bash

set -e

usage() {
    echo "Usage: $0 -a <access-key> -s <secret-key> -k <kubeconfig-path>"
    echo ""
    echo "Options:"
    echo "  -a    AWS Access Key ID"
    echo "  -s    AWS Secret Access Key"
    echo "  -k    Path to kubeconfig file"
    echo "  -h    Show this help message"
    exit 1
}

while getopts "a:s:k:h" opt; do
    case $opt in
        a) ACCESS_KEY="$OPTARG" ;;
        s) SECRET_KEY="$OPTARG" ;;
        k) KUBECONFIG_PATH="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [[ -z "$ACCESS_KEY" || -z "$SECRET_KEY" || -z "$KUBECONFIG_PATH" ]]; then
    echo "Error: All arguments are required."
    usage
fi

if [[ ! -f "$KUBECONFIG_PATH" ]]; then
    echo "Error: kubeconfig file not found: $KUBECONFIG_PATH"
    exit 1
fi

NAMESPACE="argocd"
SECRET_NAME="aws-credentials"

# Create namespace if not exists
kubectl create namespace "$NAMESPACE" --kubeconfig "$KUBECONFIG_PATH" --dry-run=client -o yaml | \
    kubectl apply --kubeconfig "$KUBECONFIG_PATH" -f -

# Create or update secret
kubectl create secret generic "$SECRET_NAME" \
    -n "$NAMESPACE" \
    --from-literal=access-key="$ACCESS_KEY" \
    --from-literal=secret-access-key="$SECRET_KEY" \
    --kubeconfig "$KUBECONFIG_PATH" \
    --dry-run=client -o yaml | \
    kubectl apply --kubeconfig "$KUBECONFIG_PATH" -f -

echo "Secret '$SECRET_NAME' created/updated in namespace '$NAMESPACE'"
