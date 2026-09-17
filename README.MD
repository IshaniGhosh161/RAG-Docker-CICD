#### Create Virtual Environment
python -m venv .venv

#### Activate the Virtual environment
.venv\Scripts\activate

#### Import required library
For a faster Windows install, install the CPU-only Torch wheel first and let pip reuse its local download cache:

```powershell
python -m pip install --upgrade pip
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

The reranker requires `torch` and `sentence-transformers`; those are the main reason the first installation is large. Do not use `--no-cache-dir` for local `.venv` setup. It saves disk space but makes repeated installs download everything again.

#### Run FastAPI app first
python -m uvicorn backend.api:app --host 0.0.0.0 --port 5000

#### Create or rebuild the FAISS index
python indexing/create_index.py

#### Run API tests (API must be running)
pytest tests

Logs are written to `log/app.log` by default. Set `LOG_LEVEL` or `LOG_FILE` to override the logging configuration.

#### Run with Docker Compose
Create a `.env` file in the project root with the required credentials:

```env
MONGO_URI=your-mongodb-uri
OLLAMA_HOST=https://ollama.com
OLLAMA_API_KEY=your-ollama-api-key
OLLAMA_MODEL=gpt-oss:20b-cloud
TAVILY_API_KEY=your-tavily-api-key
```

Start the application and observability stack:

```powershell
docker compose up -d --build
docker compose ps
```

The browser frontend and FastAPI API are available at `http://localhost:5000`.
Interactive API documentation is available at `http://localhost:5000/docs`.
Prometheus metrics are available at `http://localhost:5000/metrics`.
Prometheus is available at `http://localhost:9090`, Grafana at `http://localhost:3000`, and Jaeger traces at `http://localhost:16686`.
Grafana is provisioned with a Prometheus datasource and an API overview dashboard. The Compose API exports OpenTelemetry traces to Jaeger automatically.
Check that the API is healthy before using the dashboard:

```powershell
curl http://localhost:5000/api/health
curl http://localhost:5000/metrics
```

Open the Grafana dashboard at `http://localhost:3000/d/rag-chat-api/`. Open Jaeger at `http://localhost:16686`, select the `rag-chat-api` service, and click **Find Traces** after making API requests. Ensure your MongoDB Atlas network access list allows the Docker host IP.

To run the published Docker Hub image locally, replace `YOUR_DOCKERHUB_USERNAME` with your Docker Hub username:

```powershell
docker pull YOUR_DOCKERHUB_USERNAME/chatbot-rag:latest
$env:DOCKERHUB_IMAGE = "YOUR_DOCKERHUB_USERNAME/chatbot-rag"
docker compose --env-file path to .env\.env -f path to docker compose\docker-compose.yml up --no-build -d
OR
docker compose up -d
```

The image is published by GitHub Actions after a successful push to `main` or `master`. Configure the repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`; create the `chatbot-rag` repository on Docker Hub first. The existing CI secrets are still required for the Compose health checks.

#### Run on Kubernetes
Stop the Compose stack first if it is running, because both runtimes use the same local ports:

```powershell
docker compose down
```

Docker Desktop Kubernetes runs the complete API and observability stack from the manifests in `k8s/`. Enable Kubernetes in Docker Desktop, then select and verify the local cluster:

```powershell
kubectl config use-context docker-desktop
kubectl get nodes
```

For MongoDB Atlas SRV connections, configure Docker Desktop Kubernetes CoreDNS to use TCP for upstream DNS responses. This prevents the `ReplicaSetNoPrimary` / `dns: overflowing header size` error that can occur when an Atlas member has a long CNAME chain:

```powershell
kubectl -n kube-system edit configmap coredns
```

In the `Corefile`, change the `forward` block to include `force_tcp`:

```text
forward . /etc/resolv.conf {
	force_tcp
	max_concurrent 1000
}
```

Restart CoreDNS and verify external MongoDB DNS resolution from the API pod:

```powershell
kubectl -n kube-system rollout restart deployment/coredns
kubectl -n kube-system rollout status deployment/coredns
kubectl -n rag-chat exec deploy/api -- python -c "import socket; print(socket.gethostbyname('cluster0-shard-00-02.f5j4e.mongodb.net'))"
```

If Docker Desktop Kubernetes is reset or upgraded, reapply this CoreDNS change before deploying the API. The `kube-system` namespace is managed by Docker Desktop; do not delete it.

Create the namespace and application secret. Choose one of these options:

If you already have a real `k8s/secret.yaml` containing the `rag-secrets` Secret, apply it directly:

```powershell
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml
```

Do not commit `k8s/secret.yaml` because it contains credentials. Keep only `k8s/secret.example.yaml` in source control.

If `k8s/secret.yaml` does not exist, create the Secret from the existing `.env` file. The commands below also remove surrounding quotes from values:

```powershell
kubectl apply -f k8s/namespace.yaml
$mongoUri = ((Get-Content .env | Select-String '^MONGO_URI\s*=' | ForEach-Object { ($_ -split '=', 2)[1].Trim() }).Trim([char]34, [char]39))
$ollamaApiKey = ((Get-Content .env | Select-String '^OLLAMA_API_KEY\s*=' | ForEach-Object { ($_ -split '=', 2)[1].Trim() }).Trim([char]34, [char]39))
$tavilyApiKey = ((Get-Content .env | Select-String '^TAVILY_API_KEY\s*=' | ForEach-Object { ($_ -split '=', 2)[1].Trim() }).Trim([char]34, [char]39))
kubectl create secret generic rag-secrets `
	--namespace rag-chat `
	--from-literal="MONGO_URI=$mongoUri" `
	--from-literal="OLLAMA_API_KEY=$ollamaApiKey" `
	--from-literal="TAVILY_API_KEY=$tavilyApiKey" `
	--dry-run=client -o yaml | kubectl apply -f -
```

Then deploy the application:

```powershell
kubectl apply -k k8s/
kubectl -n rag-chat rollout status deployment/api
kubectl -n rag-chat get pods
```

The Kubernetes API uses the `ishanighosh/chatbot-rag:latest` image with `IfNotPresent`, so Docker Desktop can use the local image. Forward the API port in one terminal:

```powershell
kubectl -n rag-chat port-forward service/api 5000:5000
```

The frontend and API are then available at `http://localhost:5000`; interactive API documentation is at `http://localhost:5000/docs`. Check the deployment with:

```powershell
curl http://localhost:5000/api/health
curl http://localhost:5000/metrics
```

In separate terminals, forward the monitoring UIs:

```powershell
kubectl -n rag-chat port-forward service/prometheus 9090:9090
kubectl -n rag-chat port-forward service/grafana 3000:3000
kubectl -n rag-chat port-forward service/jaeger 16686:16686
```

Open the Grafana dashboard at `http://localhost:3000/d/rag-chat-api/`. Open Jaeger at `http://localhost:16686`, select the `rag-chat-api` service, and click **Find Traces** after making API requests. Prometheus is available at `http://localhost:9090` for direct metric queries.

To stop the Kubernetes application later:

```powershell
kubectl delete namespace rag-chat
```

#### Run the test suite
.venv\Scripts\python.exe -m pytest tests -q

GitHub Actions runs Python compilation, the test suite, and a Docker image build for pushes and pull requests to `main` or `master`.
The workflow also runs the image and checks `/api/health`. Add these repository secrets for that container check: `MONGO_URI`, `OLLAMA_HOST` (optional), `OLLAMA_API_KEY`, and `TAVILY_API_KEY`.