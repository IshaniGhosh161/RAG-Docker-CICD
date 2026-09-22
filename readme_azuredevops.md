# Azure DevOps Deployment Guide for this RAG App

This repository already includes an Azure DevOps pipeline in [azure-pipeline.yml](azure-pipeline.yml). The pipeline performs the following tasks:

- validates the Python application
- creates the FAISS index
- installs dependencies
- runs tests
- builds the Docker image
- pushes the image to Azure Container Registry (ACR)
- creates or updates the Azure Kubernetes Service (AKS) infrastructure
- deploys the app to Kubernetes

This guide is written for a beginner and explains every major step clearly.

Important note for personal accounts:

- Azure DevOps is separate from Azure cloud billing.
- You can use Azure DevOps with a personal Microsoft account.
- But the actual deployment to Azure resources like AKS and ACR needs a valid Azure subscription and permissions.
- If you are on a free personal account, make sure your Azure subscription is active and has enough access.

---

## 1) What this project deploys

This app is deployed to Azure Kubernetes Service (AKS). The pipeline creates and configures these Azure resources:

- Azure Resource Group
- Azure Container Registry (ACR)
- Azure Kubernetes Service (AKS)
- Log Analytics workspace
- Kubernetes namespace and app services
- Application secrets for MongoDB, Ollama, Tavily, and email config

The infrastructure is defined in [infra/main.bicep](infra/main.bicep), and the Kubernetes deployment files are in [k8s/azure](k8s/azure).

---

## 2) Prerequisites

Before you start, make sure you have these ready:

- A valid Azure subscription
- A Microsoft account or business account that can sign in to Azure DevOps
- Azure DevOps organization and project access
- Permission to create Azure resources in the subscription
- A GitHub or Azure Repos repository containing this project
- A branch named `azure` in the repo
- The following secret values:
  - `MONGO_URI`
  - `OLLAMA_API_KEY`
  - `TAVILY_API_KEY`
  - `GRAFANA_SMTP_USER`
  - `GRAFANA_SMTP_PASSWORD`

Example values:

```text
MONGO_URI = mongodb+srv://<username>:<password>@<cluster-host>/chatbot?retryWrites=true&w=majority
OLLAMA_API_KEY = <your-ollama-key>
TAVILY_API_KEY = <your-tavily-key>
GRAFANA_SMTP_USER = <smtp-user>
GRAFANA_SMTP_PASSWORD = <smtp-password>
```

---

## 3) Create an Azure subscription if needed

If you are a brand new Azure user, do this first:

1. Open https://portal.azure.com
2. Sign in with your Microsoft account
3. Click on Subscriptions
4. Check whether a subscription is already active
5. If no subscription is active, create or activate a free Azure account or trial

For this project, AKS and ACR are not completely free. A valid subscription is required.

If your account is personal and free-tier, the deployment will still work only after the Azure subscription is active and usable.

---

## 4) Open Azure DevOps

Use the Azure DevOps website:

```text
https://dev.azure.com
```

If it redirects to Microsoft login or Azure Portal, that is normal. Microsoft login is used for authentication. After signing in successfully, it will return to Azure DevOps.

### Step-by-step

1. Open https://dev.azure.com
2. Sign in with your Microsoft account or organization account
3. If prompted, create a new Azure DevOps organization
4. Create a new project inside that organization
5. Give the project a simple name such as `rag-chat-project`

Example:

- Organization: `yourname` or `yourcompany`
- Project: `rag-chat-project`

Once the project is created, you will see the Azure DevOps dashboard.

---

## 5) Understand the difference between Azure DevOps and Azure Portal

This is very important for beginners:

- Azure DevOps = code, CI/CD, repos, pipelines, service connections
- Azure Portal = cloud resources like AKS, ACR, resource groups, networking

For this project:

- You use Azure DevOps to create and run the pipeline
- You use Azure Portal to verify Azure resource creation

---

## 6) Create the Azure DevOps project

After signing in to Azure DevOps:

1. Click New Project
2. Enter the project name
3. Select Visibility:
   - Private or Public (for personal use, Private is usually fine)
4. Select the default version control
   - Git is recommended
5. Click Create

After creation, you will land on the project dashboard.

---

## 7) Connect this repository to Azure DevOps

You must connect the repo before creating the pipeline.

### Option A: Use GitHub

1. In Azure DevOps project, click Repos
2. Click Import repository
3. Select GitHub
4. Authorize GitHub access
5. Select the repository for this project
6. Import it

### Option B: Use Azure Repos

If your repo is already in Azure Repos:

1. Click Repos
2. Choose Files
3. Click Import Repository
4. Select the repo source

After import, the repository is available in Azure DevOps.

---

## 8) Prepare the branch for deployment

The pipeline in [azure-pipeline.yml](azure-pipeline.yml) is configured to trigger on the `azure` branch.

Before running the pipeline, make sure the repo has that branch.

Run locally:

```bash
git checkout -b azure
git push origin azure
```

If you already have a branch named `azure`, just push to it.

If the project is imported into Azure DevOps, make sure the branch is selected correctly when creating the pipeline.

---

## 9) Check the actual pipeline file

Open [azure-pipeline.yml](azure-pipeline.yml) and read the key values.

Important variables in the pipeline:

```yaml
variables:
  pythonVersion: '3.12'
  RESOURCE_GROUP: 'rag-chat-rg'
  LOCATION: 'eastus'
  ACR_NAME: 'ragchatacr2026'
  AKS_NAME: 'ragchat-aks'
  NAMESPACE: 'rag-chat'
  AZURE_SERVICE_CONNECTION: 'azure-ragchat'
```

This tells Azure DevOps:

- which Azure region to use (`eastus`)
- what resource group name to create (`rag-chat-rg`)
- what ACR name to use (`ragchatacr2026`)
- what AKS cluster name to create (`ragchat-aks`)
- where to deploy the app (`rag-chat` namespace)
- which Azure service connection to use (`azure-ragchat`)

If you want different names, update the pipeline before running it.

---

## 10) Create the Azure service connection

This is one of the most important steps.

The pipeline needs a connection to Azure so it can create the resource group, ACR, AKS, and Kubernetes deployment.

The expected service connection name is:

- `azure-ragchat`

### How to create it

1. In Azure DevOps, open your project
2. In the left menu, click Project Settings
3. Click Service connections
4. Click New service connection
5. Choose Azure Resource Manager
6. Select Service principal (automatic) is the easiest option
7. Choose your Azure subscription
8. Select the correct tenant
9. Give it the exact name:
   
   `azure-ragchat`

10. Click Save

### If the name is different

If you use a different name, update the YAML file:

```yaml
AZURE_SERVICE_CONNECTION: 'your-other-name'
```

> You do not need to rename everything else; just make the name match the connection you created.

---

## 11) Check Azure subscription permissions

Before the pipeline runs, confirm your account can create Azure resources.

In the Azure Portal:

1. Open https://portal.azure.com
2. Sign in
3. Open Subscriptions
4. Select the active subscription
5. Confirm that you have permission to create resource groups and AKS resources

If you are using a personal account, ensure the subscription is not locked or restricted.

---

## 12) Create the variable group for app secrets

The pipeline uses Azure DevOps variables to create Kubernetes secrets in the cluster.

The secret names are defined in the pipeline:

```bash
kubectl -n "$(NAMESPACE)" create secret generic rag-secrets \
  --from-literal=MONGO_URI="$(MONGO_URI)" \
  --from-literal=OLLAMA_API_KEY="$(OLLAMA_API_KEY)" \
  --from-literal=TAVILY_API_KEY="$(TAVILY_API_KEY)" \
  --from-literal=GRAFANA_SMTP_USER="$(GRAFANA_SMTP_USER)" \
  --from-literal=GRAFANA_SMTP_PASSWORD="$(GRAFANA_SMTP_PASSWORD)" \
```

### Create variable group in Azure DevOps

1. In Azure DevOps, open Pipelines
2. Click Library
3. Click Variable groups
4. Click Add variable group
5. Name it something like `rag-chat-secrets`
6. Add the following variables:

```text
MONGO_URI = mongodb+srv://<username>:<password>@<cluster-host>/chatbot?retryWrites=true&w=majority
OLLAMA_API_KEY = <your-ollama-api-key>
TAVILY_API_KEY = <your-tavily-api-key>
GRAFANA_SMTP_USER = <smtp-user>
GRAFANA_SMTP_PASSWORD = <smtp-password>
```

7. Save it

Then link this variable group to the pipeline if needed.

> If you do not add these variables, the Kubernetes secret step in the pipeline will fail.

---

## 13) Understand the deployment files in this repo

Before running the pipeline, read the files that drive the deployment:

- [azure-pipeline.yml](azure-pipeline.yml) — pipeline logic for build and deployment
- [infra/main.bicep](infra/main.bicep) — Azure infrastructure definition
- [k8s/azure/api.yaml](k8s/azure/api.yaml) — Kubernetes deployment and service
- [k8s/azure/configmap.yaml](k8s/azure/configmap.yaml) — app config values
- [k8s/azure/secret.example.yaml](k8s/azure/secret.example.yaml) — sample secret file

These files work together to create the app environment.

---

## 14) Create the pipeline in Azure DevOps

Now you are ready to create the pipeline.

### Steps

1. In Azure DevOps, click Pipelines
2. Click New pipeline
3. Select Azure Repos Git
4. Choose the repository you imported
5. Select Existing Azure Pipelines YAML file
6. Choose the file path:
   
   [azure-pipeline.yml](azure-pipeline.yml)

7. Click Continue
8. Review the pipeline and click Save

The pipeline is now in Azure DevOps.

---

## 15) Run the pipeline manually

After the pipeline is created, run it.

### Steps

1. Open Pipelines
2. Click the pipeline name
3. Click Run pipeline
4. Select the `azure` branch
5. Make sure the parameter `deploy` is set to `true`
6. Click Run

This starts the pipeline and runs all stages.

---

## 16) Understand what the pipeline does

The pipeline has two main stages:

### Stage 1: Validate

This stage validates the app before Azure deployment.

It does the following:

- checks out the repository
- installs Python 3.12
- installs project dependencies from `requirements.txt` and `requirements-dev.txt`
- creates the FAISS index with `python indexing/create_index.py`
- compiles the Python files
- runs tests with `python -m pytest tests -q`

If the validation stage fails, the deployment stage will not run.

### Stage 2: AzureDeploy

This stage deploys the app to Azure.

It does the following:

- creates the Azure resource group
- registers required Azure providers
- deploys the Bicep file from [infra/main.bicep](infra/main.bicep)
- creates Azure Container Registry (ACR)
- builds the Docker image for the app
- pushes the Docker image to ACR
- gets the AKS cluster credentials
- creates the Kubernetes namespace
- creates Kubernetes secrets
- applies the app manifests from [k8s/azure](k8s/azure)
- checks the deployment rollout status

---

## 17) What happens behind the scenes during deployment

The app is deployed to AKS with these building blocks:

### A. Azure infrastructure

The Bicep file creates:

- a resource group
- ACR
- AKS
- Log Analytics
- RBAC assignment so AKS can pull images from ACR

### B. Docker image build

The pipeline builds this project into a Docker image and pushes it to ACR.

### C. Kubernetes deployment

The app is run inside Kubernetes using manifests in [k8s/azure](k8s/azure).

- `api.yaml` defines the Deployment and Service
- `configmap.yaml` sets configuration
- secrets hold private values such as database and API keys

---

## 18) Verify Azure resources after deployment

After the pipeline finishes, verify the resources in Azure Portal.

### Check the resource group

Open Azure Portal and search for Resource groups.

You should see:

- `rag-chat-rg`

### Check AKS cluster

Open Azure Portal and search for Kubernetes services.

Look for:

- `ragchat-aks`

### Check ACR

Open Container registries and look for:

- `ragchatacr2026`

### Check the app in Kubernetes

You can also use Azure CLI locally:

```bash
az login
az account set --subscription "<subscription-id-or-name>"
az aks get-credentials --resource-group rag-chat-rg --name ragchat-aks --overwrite-existing
kubectl get ns
kubectl -n rag-chat get pods
kubectl -n rag-chat get svc
kubectl -n rag-chat get deployment
```

---

## 19) Verify the app is running

The application exposes a health check endpoint.

First, get the external IP:

```bash
kubectl -n rag-chat get svc api -o wide
```

Then open in a browser:

```text
http://<external-ip>/api/health
```

Useful URLs:

- `http://<external-ip>/api/health`
- `http://<external-ip>/docs`
- `http://<external-ip>/metrics`

If the app is still starting, wait a few minutes and check again.

---

## 20) Troubleshooting for beginners

### Problem: Azure DevOps redirects to Azure Portal

This is normal. Microsoft sign-in often redirects through the Azure identity system. Sign in again there and return to Azure DevOps.

### Problem: service connection not found

Check that the service connection name matches exactly:

```text
azure-ragchat
```

### Problem: no Azure subscription is available

Go to Azure Portal and check your subscriptions.

If there is no subscription, create or activate a free subscription/trial.

### Problem: pipeline fails in Validate stage

Common reasons:

- package installation failed
- PDF data folder is missing or empty
- tests failed
- environment variables are not set correctly

### Problem: AKS deployment fails

Common reasons:

- ACR name is not unique
- resource quota is insufficient
- service connection is not valid
- Azure region is not supported

### Problem: app pod is crashing

Run these commands:

```bash
kubectl -n rag-chat get pods
kubectl -n rag-chat describe pod <pod-name>
kubectl -n rag-chat logs deployment/api --tail=200
```

Also check the secrets and config map:

```bash
kubectl -n rag-chat get secret rag-secrets -o yaml
kubectl -n rag-chat get configmap rag-config -o yaml
```

### Problem: service has no external IP

Run:

```bash
kubectl -n rag-chat get svc api
kubectl -n rag-chat describe svc api
```

This usually means Azure is still provisioning the Load Balancer.

---

## 21) Redeploy after changes

Whenever you change code and push to the `azure` branch, you can redeploy manually.

### Steps

1. Push your code to the `azure` branch
2. Open Azure DevOps
3. Go to Pipelines
4. Select the pipeline
5. Click Run pipeline
6. Choose the `azure` branch
7. Run again

If you want a quick Kubernetes refresh, run:

```bash
kubectl -n rag-chat apply -k k8s/azure
kubectl -n rag-chat rollout restart deployment/api
kubectl -n rag-chat rollout status deployment/api
```

---

## 22) Final checklist before you finish

Before calling the deployment complete, confirm all of these:

- [ ] Azure subscription is active
- [ ] Azure DevOps organization and project were created
- [ ] the repository is connected to Azure DevOps
- [ ] the `azure` branch exists
- [ ] service connection `azure-ragchat` exists
- [ ] all required secret variables are added
- [ ] the pipeline was created from [azure-pipeline.yml](azure-pipeline.yml)
- [ ] the Validate stage passed
- [ ] Azure resources were created
- [ ] the ACR image was pushed successfully
- [ ] the AKS cluster is running
- [ ] the Kubernetes namespace `rag-chat` exists
- [ ] the app deployment is running
- [ ] the service has an external IP
- [ ] `/api/health` is responding

---

## 23) Final result

When all checks pass, your app will be deployed to Azure AKS and will be accessible through a public external IP. Azure DevOps will be managing the build and deployment flow automatically through the pipeline in [azure-pipeline.yml](azure-pipeline.yml).

This completes the Azure DevOps deployment setup for this project.

If you want, the next step can be to add:

- environment approvals
- automatic deployment on every push to `azure`
- release stages
- Azure Container Apps instead of AKS
- production-ready monitoring and alerts
