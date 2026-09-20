#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rag-chat-rg}"
LOCATION="${LOCATION:-eastus}"
ACR_NAME="${ACR_NAME:-ragchatacr12345}"
AKS_NAME="${AKS_NAME:-ragchat-aks}"
IMAGE_NAME="${IMAGE_NAME:-rag-chat}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
NAMESPACE="${NAMESPACE:-rag-chat}"

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" >/dev/null

az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic >/dev/null

az aks create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$AKS_NAME" \
  --node-count 2 \
  --generate-ssh-keys \
  --attach-acr "$ACR_NAME" >/dev/null

az acr build \
  --registry "$ACR_NAME" \
  --image "$IMAGE_NAME:$IMAGE_TAG" \
  .

az aks get-credentials \
  --resource-group "$RESOURCE_GROUP" \
  --name "$AKS_NAME" \
  --overwrite-existing

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "$NAMESPACE" create secret generic rag-secrets \
  --from-literal=MONGO_URI="${MONGO_URI:?Set MONGO_URI before deployment}" \
  --from-literal=OLLAMA_API_KEY="${OLLAMA_API_KEY:-}" \
  --from-literal=TAVILY_API_KEY="${TAVILY_API_KEY:-}" \
  --from-literal=GRAFANA_SMTP_USER="${GRAFANA_SMTP_USER:-}" \
  --from-literal=GRAFANA_SMTP_PASSWORD="${GRAFANA_SMTP_PASSWORD:-}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "$NAMESPACE" apply -k k8s/azure

echo "Deployment complete."
echo "Check API service: kubectl -n $NAMESPACE get svc api"
echo "Check cluster: az aks show --resource-group $RESOURCE_GROUP --name $AKS_NAME --query "provisioningState""
