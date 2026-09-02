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
docker compose up --build

The browser frontend and FastAPI API are available at `http://localhost:5000`.
Interactive API documentation is available at `http://localhost:5000/docs`.
Prometheus metrics are available at `http://localhost:5000/metrics`.
Prometheus is available at `http://localhost:9090`, Grafana at `http://localhost:3000`, and Jaeger traces at `http://localhost:16686`.
Grafana is provisioned with a Prometheus datasource and an API overview dashboard. The Compose API exports OpenTelemetry traces to Jaeger automatically.
Set your MongoDB Atlas connection string as `MONGO_URI` in `.env`, along with `OLLAMA_HOST`, `OLLAMA_API_KEY`, and `TAVILY_API_KEY` when required. Ensure your Docker host IP is allowed in the Atlas network access list.

To run the published Docker Hub image locally, replace `YOUR_DOCKERHUB_USERNAME` with your Docker Hub username:

```powershell
docker pull YOUR_DOCKERHUB_USERNAME/chatbot-rag:latest
$env:DOCKERHUB_IMAGE = "YOUR_DOCKERHUB_USERNAME/chatbot-rag"
docker compose --env-file path to .env\.env -f path to docker compose\docker-compose.yml up --no-build -d
OR
docker compose up -d
```

The image is published by GitHub Actions after a successful push to `main` or `master`. Configure the repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`; create the `chatbot-rag` repository on Docker Hub first. The existing CI secrets are still required for the Compose health checks.

#### Run the test suite
.venv\Scripts\python.exe -m pytest tests -q

GitHub Actions runs Python compilation, the test suite, and a Docker image build for pushes and pull requests to `main` or `master`.
The workflow also runs the image and checks `/api/health`. Add these repository secrets for that container check: `MONGO_URI`, `OLLAMA_HOST` (optional), `OLLAMA_API_KEY`, and `TAVILY_API_KEY`.