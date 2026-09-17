# RAG Chatbot Project Setup

This project is a FastAPI + FAISS + Ollama RAG application with MongoDB user/session storage, Prometheus/Grafana monitoring, and a simple frontend. To complete the project successfully, you must set up all required services and environment variables before starting the app.

## 1) Prerequisites

Install the following before continuing:

- Python 3.10+ (3.11 is recommended)
- Git
- Docker Desktop with Docker Compose enabled
- A MongoDB database, preferably MongoDB Atlas
- An Ollama model endpoint or cloud account
- A Tavily account for web search

For Windows PowerShell, use commands like this:

```powershell
python --version
docker --version
docker compose version
```

If any of those fail, install the missing tool first.

## 2) Clone the repo and create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 3) Install Python dependencies

This project depends on `torch`, `sentence-transformers`, FAISS, FastAPI, MongoDB, and LangChain packages. Use the commands below in the activated virtual environment:

```powershell
python -m pip install --upgrade pip
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

Do not use `--no-cache-dir` for local development; the reranker and embedding packages are large and repeated installs will download them again.

## 4) Get the required API keys and connection strings

You need these values before the app can start.

### MongoDB URI (`MONGO_URI`)

Create or use a MongoDB Atlas cluster, then get the connection string from Atlas:

1. Go to MongoDB Atlas
2. Create a cluster if needed
3. Click "Database" > "Connect"
4. Choose "Drivers"
5. Copy the connection string
6. Replace `<password>` with your actual MongoDB password

Example:

```env
MONGO_URI=mongodb+srv://myuser:MyStrongPassword@cluster0.xxxxxx.mongodb.net/chatbot?retryWrites=true&w=majority
```

Important:

- Your database user must exist in Atlas
- Add your local machine IP and your Docker host IP to Atlas Network Access if using Atlas
- The app uses MongoDB for users, chat sessions, and message storage

### Ollama settings (`OLLAMA_HOST`, `OLLAMA_API_KEY`, `OLLAMA_MODEL`)

The repo is configured to use Ollama for the LLM.

- Default host: `https://ollama.com`
- Default model: `gpt-oss:20b-cloud`
- If you use the hosted Ollama service, get an API key from the Ollama dashboard
- If you run Ollama locally, set `OLLAMA_HOST` to your local URL, for example `http://localhost:11434` or `http://host.docker.internal:11434`

Example values:

```env
OLLAMA_HOST=https://ollama.com
OLLAMA_API_KEY=your_ollama_api_key_here
OLLAMA_MODEL=gpt-oss:20b-cloud
```

For local Ollama, this is also valid:

```env
OLLAMA_HOST=http://localhost:11434
OLLAMA_API_KEY=
OLLAMA_MODEL=llama3.1:8b
```

Note: the Docker Compose file currently requires `OLLAMA_API_KEY` to be set. For Docker or CI runs, use a valid Ollama cloud key. For local-only development, set `OLLAMA_HOST` to your local Ollama URL and still provide a non-empty value in the compose environment if required by your setup.

### Tavily API key (`TAVILY_API_KEY`)

This app uses Tavily for web search during retrieval. Get a key from:

- https://app.tavily.com

Example:

```env
TAVILY_API_KEY=your_tavily_api_key_here
```

### Optional settings

```env
LOG_LEVEL=INFO
LOG_FILE=api.log
HF_EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5
HF_EMBEDDING_DEVICE=cpu
HF_EMBEDDING_BATCH_SIZE=8
```

## 5) Create the `.env` file

Create a file named `.env` in the project root and add the values below:

```env
MONGO_URI=mongodb+srv://myuser:MyStrongPassword@cluster0.xxxxxx.mongodb.net/chatbot?retryWrites=true&w=majority
OLLAMA_HOST=https://ollama.com
OLLAMA_API_KEY=your_ollama_api_key_here
OLLAMA_MODEL=gpt-oss:20b-cloud
TAVILY_API_KEY=your_tavily_api_key_here
LOG_LEVEL=INFO
LOG_FILE=api.log
HF_EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5
HF_EMBEDDING_DEVICE=cpu
HF_EMBEDDING_BATCH_SIZE=8
```

This project reads `.env` automatically with `python-dotenv`.

## 6) Prepare your document data

The indexing script expects PDF files in the `data/` folder.

1. Create or use the `data` directory in the repo root
2. Put your PDF documents there
3. The script will read all `.pdf` files in `data/`

Example:

```powershell
mkdir data
```

Then place your PDFs inside `data/`.

## 7) Build the FAISS index

Before the agent can answer questions from documents, you must create the vector index.

```powershell
python indexing/create_index.py
```

This script:

- loads every PDF in `data/`
- splits the content into chunks
- embeds each chunk with the configured Nomic model
- creates the FAISS index under `index/faiss_index/`

If this step fails, check the PDF folder and your environment values.

## 8) Run the API locally

Start the FastAPI app in the virtual environment:

```powershell
python -m uvicorn backend.api:app --host 0.0.0.0 --port 5000
```

Then open:

- API: http://localhost:5000
- Swagger docs: http://localhost:5000/docs
- Metrics: http://localhost:5000/metrics

The app exposes a frontend and backend endpoints such as:

- `/api/health`
- `/api/register`
- `/api/login`
- `/api/sessions`
- `/api/sessions/{session_id}/generate`

## 9) Run tests

Once the API is running, run:

```powershell
pytest tests
```

or

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

The tests expect the app to be running on `http://localhost:5000` by default.

## 10) Run with Docker Compose

This project also includes Docker Compose for the API, Prometheus, Grafana, and Jaeger.

From the project root:

```powershell
docker compose up --build
```

The stack exposes:

- App: http://localhost:5000
- Swagger docs: http://localhost:5000/docs
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000
- Jaeger UI: http://localhost:16686

Docker Compose reads environment values from `.env` and requires `MONGO_URI` and `OLLAMA_API_KEY` to be present in the environment.

To stop it:

```powershell
docker compose down
```

## 11) Test suite

The repository includes API tests in `tests/test_api.py`. The workflow runs them with:

```powershell
python -m pytest tests -q
```

or

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

The tests expect the app to be running on `http://localhost:5000` unless you set `API_BASE_URL` before running them.

Example:

```powershell
$env:API_BASE_URL = "http://localhost:5000"
pytest tests
```

The test suite checks:

- health endpoint
- metrics endpoint
- frontend/static file serving
- error handling
- CORS headers
- registration/login flow
- session creation and message saving
- generation endpoint and streaming endpoint

## 12) Docker pull from Docker Hub

This project publishes a Docker image to Docker Hub during GitHub Actions runs on pushes to `main` or `master`.

To pull the published image locally:

```powershell
docker pull YOUR_DOCKERHUB_USERNAME/chatbot-rag:latest
```

Then run it with your environment file:

```powershell
$env:DOCKERHUB_IMAGE = "YOUR_DOCKERHUB_USERNAME/chatbot-rag"
docker compose --env-file .env up --no-build -d
```

You can also run the image directly:

```powershell
docker run -d --name chatbot-rag -p 5000:5000 --env-file .env YOUR_DOCKERHUB_USERNAME/chatbot-rag:latest
```

Before pushing or pulling a published image, create the repository on Docker Hub and set the required secrets in GitHub:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

## 13) GitHub Actions workflow

The repo includes a CI workflow in `.github/workflows/cicd.yml`.

What it does:

1. Sets up Python and installs dependencies
2. Creates the FAISS index from the PDF files in `data/`
3. Runs Python compilation checks
4. Runs the full test suite with `python -m pytest tests -q`
5. Builds the Docker Compose stack
6. Verifies the app and frontend are reachable on `http://localhost:5000`
7. Logs in to Docker Hub and pushes the image on `push` events
8. Shuts down the Docker stack at the end

Required GitHub repository secrets for the workflow:

- `MONGO_URI`
- `OLLAMA_API_KEY`
- `TAVILY_API_KEY`
- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

The workflow is triggered on pushes and pull requests to `main` and `master`.

## 14) Agent architecture

This project follows a RAG agent pattern built around a LangGraph workflow in `scripts/agent.py`.

For a deeper explanation of the agent design, review the project architecture document: `Chatbot Architecture.pdf` in the docs/project documentation set. It provides the full system diagram and design rationale behind the agent flow.

### High-level flow

1. The FastAPI app receives a chat request from the frontend or API client.
2. The request is tied to a MongoDB user session and saved to the database.
3. The agent loads the configured FAISS vector index and uses an embedding model to retrieve relevant document chunks.
4. A reranker can improve the initial retrieval ordering using a cross-encoder model.
5. The workflow decides whether the question should trigger:
   - a vector search
   - a web search via Tavily
   - a direct LLM call
6. The LLM generates the final answer using the retrieved context, chat history, and prompt templates.
7. The result is saved back to MongoDB as an assistant message and returned to the client.

### Main components

- `backend/api.py`: FastAPI app, REST endpoints, CORS, health checks, and streaming endpoints
- `backend/database.py`: MongoDB access for users, sessions, messages, and authentication
- `backend/config.py`: environment-driven runtime settings
- `indexing/create_index.py`: PDF ingestion, chunking, embedding, and FAISS index creation
- `scripts/agent.py`: main LangGraph agent orchestration and retrieval flow
- `backend/embeddings.py`: Nomic embedding wrapper used for vector search
- `prompts/`: prompt templates for query building, routing, answer generation, grading, and hallucination checks

### Retrieval and reasoning flow

The agent uses a graph-based flow similar to this:

- `build_query` decides how to handle the incoming question
- `retrieve` fetches similar documents from FAISS
- `rerank` reorders documents for relevance
- `grade_documents` decides whether the retrieved context is adequate
- `transform_query` rewrites the query if the initial retrieval was weak
- `web_search` fetches live results from Tavily when needed
- `generate` creates the final answer
- `grade_generation` validates whether the response is supported by the retrieved evidence

### Route-query logic

The project includes a prompt-driven route decision step in `prompts/route_query.txt`, and the workflow uses it to decide which path is best for the user question.

The routing logic is designed to choose between three main strategies:

- `vectorstore`: use the local FAISS index when the answer is likely grounded in uploaded PDF content
- `web-search`: use Tavily when the user asks about recent or external knowledge not covered by the local documents
- `llm`: respond directly when the query is general, conversational, or does not require document grounding

In practice, the route step looks at the user’s intent and chooses the shortest reliable path:

- knowledge-base questions -> retrieval from FAISS
- live or updated information -> web search
- simple conversational queries -> direct LLM answer

This routing step keeps the system efficient and helps avoid unnecessary retrieval or internet lookup when the answer can be produced directly.

### Why this architecture matters

This design lets the project do three important things:

- use local domain documents as a grounded knowledge source
- augment missing information with live web search when needed
- keep session context and user data in MongoDB while the LLM handles reasoning

### Runtime dependencies

The agent architecture depends on:

- MongoDB for persistence
- FAISS for in-memory vector search
- Nomic embeddings for document encoding
- Ollama-hosted LLM for chat generation
- Tavily for external search
- LangGraph for workflow orchestration

## 15) Common issues and fixes

### `MONGO_URI` is missing or invalid

- Make sure `.env` exists in the project root
- Confirm the string is correct and your Mongo DB user has access
- Add your IP to Atlas network access if using Atlas

### `OLLAMA_API_KEY` is missing

- For the Docker Compose stack, this is required by the repo configuration
- If using an Ollama cloud account, set the real key
- If using a local Ollama instance, set `OLLAMA_HOST` to your local URL and provide the required value for the compose stack

### FAISS index not found

Run:

```powershell
python indexing/create_index.py
```

Then verify the folder exists:

```powershell
dir index\faiss_index
```

### App fails to start

Check these items:

- `.env` file exists and is in the project root
- `MONGO_URI` is valid
- `data/` contains at least one PDF
- `OLLAMA_HOST` points to a reachable server
- `TAVILY_API_KEY` is valid if you use web search

## 16) Final working flow

If you want the shortest route to a working setup, follow these steps exactly:

1. Create and activate the virtual environment
2. Install all dependencies
3. Create `.env` with `MONGO_URI`, `OLLAMA_HOST`, `OLLAMA_API_KEY`, `OLLAMA_MODEL`, and `TAVILY_API_KEY`
4. Add PDFs to `data/`
5. Run `python indexing/create_index.py`
6. Run `python -m uvicorn backend.api:app --host 0.0.0.0 --port 5000`
7. Open `http://localhost:5000/docs`
8. Validate with `pytest tests`
9. Or run `docker compose up --build` for the Docker stack
10. If publishing or validating CI, push to GitHub and let the workflow build and publish the image

This is the working setup path for this repository and is the path the project is built around.

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
```

Run the MongoDB DNS check after the API deployment is created and its pod is ready:

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
kubectl -n rag-chat exec deploy/api -- python -c "import socket; print(socket.gethostbyname('cluster0-shard-00-02.f5j4e.mongodb.net'))"
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