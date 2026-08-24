#### Create Virtual Environment
python -m venv .venv

#### Activate the Virtual environment
.venv\Scripts\activate

#### Import required library
pip install -r requirements.txt

#### Run Flask app first
python api.py

#### Run streamlit app
streamlit run app.py --server.port=8502

#### Create or rebuild the FAISS index
python index/create_index.py

#### Run API tests (API must be running)
pytest test

Logs are written to `log/app.log` by default. Set `LOG_LEVEL` or `LOG_FILE` to override the logging configuration.

#### Run with Docker Compose
docker compose up --build

The Streamlit UI is available at `http://localhost:8502` and the Flask API at `http://localhost:5000`.
Set your MongoDB Atlas connection string as `MONGO_URI` in `.env`, along with `OLLAMA_HOST`, `OLLAMA_API_KEY`, and `TAVILY_API_KEY` when required. Ensure your Docker host IP is allowed in the Atlas network access list.

To run the published Docker Hub image locally, replace `YOUR_DOCKERHUB_USERNAME` with your Docker Hub username:

```powershell
docker pull YOUR_DOCKERHUB_USERNAME/chatbot-rag:latest
$env:DOCKERHUB_IMAGE = "YOUR_DOCKERHUB_USERNAME/chatbot-rag"
docker compose up -d
```

The image is published by GitHub Actions after a successful push to `main` or `master`. Configure the repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`; create the `chatbot-rag` repository on Docker Hub first. The existing CI secrets are still required for the Compose health checks.

#### Run the test suite
.venv\Scripts\python.exe -m pytest test -q

GitHub Actions runs Python compilation, the test suite, and a Docker image build for pushes and pull requests to `main` or `master`.
The workflow also runs the image and checks `/api/health`. Add these repository secrets for that container check: `MONGO_URI`, `OLLAMA_HOST` (optional), `OLLAMA_API_KEY`, and `TAVILY_API_KEY`.