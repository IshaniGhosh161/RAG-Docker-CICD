from dotenv import load_dotenv
import os
import sys
from langchain_ollama import ChatOllama
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging_config

logger = logging.getLogger(__name__)
load_dotenv()
os.environ["OLLAMA_API_KEY"] = os.getenv("OLLAMA_API_KEY")
llm = ChatOllama(
    model="gpt-oss:120b-cloud",
    temperature=0
)

response = llm.invoke(
    "Explain LangGraph in simple terms"
)

logger.info("Ollama response: %s", response.content)