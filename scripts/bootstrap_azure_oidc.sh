#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-rag-chat-github-cicd}"
SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rag-chat-rg}"
LOCATION="${LOCATION:-eastus}"

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required but not installed."
  exit 1
fi

echo "[1/5] Ensuring Azure login"
az account show >/dev/null

echo "[2/5] Creating Azure AD app registration"
APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [ -z "$APP_ID" ]; then
  APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
fi

echo "Application ID: $APP_ID"

SP_OBJECT_ID=$(az ad sp list --filter "appId eq '$APP_ID'" --query "[0].id" -o tsv)
if [ -z "$SP_OBJECT_ID" ]; then
  az ad sp create --id "$APP_ID" >/dev/null
fi

SP_OBJECT_ID=$(az ad sp list --filter "appId eq '$APP_ID'" --query "[0].id" -o tsv)

ROLE_ASSIGNMENT_NAME="${ROLE_ASSIGNMENT_NAME:-github-cicd-contributor}"
if ! az role assignment list --assignee-object-id "$SP_OBJECT_ID" --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" --query "[].id" -o tsv | grep -q .; then
  az role assignment create \
    --assignee-object-id "$SP_OBJECT_ID" \
    --assignee-principal-type ServicePrincipal \
    --role Contributor \
    --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" >/dev/null
fi

if ! az role assignment list --assignee-object-id "$SP_OBJECT_ID" --scope "/subscriptions/$SUBSCRIPTION_ID" --query "[].id" -o tsv | grep -q .; then
  az role assignment create \
    --assignee-object-id "$SP_OBJECT_ID" \
    --assignee-principal-type ServicePrincipal \
    --role Contributor \
    --scope "/subscriptions/$SUBSCRIPTION_ID" >/dev/null
fi

echo "[3/5] Creating GitHub OIDC federated credential"
if az ad app federated-credential list --id "$APP_ID" --query "[].name" -o tsv | grep -q "github-main"; then
  echo "Federated credential already exists."
else
  az ad app federated-credential create \
    --id "$APP_ID" \
    --parameters '{
      "name": "github-main",
      "issuer": "https://token.actions.githubusercontent.com",
      "subject": "repo:YOUR_GITHUB_USERNAME/YOUR_REPO_NAME:ref:refs/heads/main",
      "audiences": ["api://AzureADTokenExchange"]
    }' >/dev/null
fi

echo "[4/5] Creating resource group"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" >/dev/null

echo "[5/5] Required GitHub secrets"
echo "AZURE_CLIENT_ID=$APP_ID"
echo "AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)"
echo "AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID"

echo ""
echo "Add these to GitHub repository secrets:"
echo "- AZURE_CLIENT_ID"
echo "- AZURE_TENANT_ID"
echo "- AZURE_SUBSCRIPTION_ID"
echo "- MONGO_URI"
echo "- OLLAMA_API_KEY"
echo "- TAVILY_API_KEY"
echo "- GRAFANA_SMTP_USER"
echo "- GRAFANA_SMTP_PASSWORD"

echo ""
echo "Then update the federated credential subject to your actual repository:"
echo "repo:<your-github-user>/<your-repo>:ref:refs/heads/main"
