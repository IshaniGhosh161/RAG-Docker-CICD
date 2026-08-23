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

#### Run the test suite
.venv\Scripts\python.exe -m pytest test -q

GitHub Actions runs Python compilation, the test suite, and a Docker image build for pushes and pull requests to `main` or `master`.
The workflow also runs the image and checks `/api/health`. Add these repository secrets for that container check: `MONGO_URI`, `OLLAMA_HOST` (optional), `OLLAMA_API_KEY`, and `TAVILY_API_KEY`.