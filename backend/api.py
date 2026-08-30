import json
import logging
import os
import time
from functools import lru_cache

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.database import DatabaseManager
from backend import logging_config
from backend.observability import configure_observability, record_rag_metrics

logger = logging.getLogger(__name__)
db = DatabaseManager()


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str


class LoginRequest(BaseModel):
    username: str
    password: str


class DeleteUserRequest(BaseModel):
    password: str


class SessionRequest(BaseModel):
    username: str
    session_name: str | None = None


class MessageRequest(BaseModel):
    username: str
    message: str


@lru_cache(maxsize=1)
def get_agent():
    from scripts.agent import Agent

    return Agent("shared")


def create_app():
    app = FastAPI(title="Chatbot API", version="1.0.0")
    project_root = os.path.dirname(os.path.dirname(__file__))
    frontend_path = os.path.join(project_root, "frontend", "index.html")
    app.mount("/frontend", StaticFiles(directory=os.path.join(project_root, "frontend")), name="frontend")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    configure_observability(app)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Invalid request data"},
        )

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return JSONResponse(status_code=404, content={"error": "Endpoint not found"})

    @app.get("/", include_in_schema=False)
    def frontend():
        return FileResponse(frontend_path)

    @app.get("/api/health")
    def health_check():
        return {"status": "healthy", "message": "Chatbot API Running"}

    @app.post("/api/register", status_code=201)
    def register(data: RegisterRequest):
        if db.register_user(data.username, data.password, data.email):
            return {"message": "User registered successfully"}
        return JSONResponse(
            status_code=409, content={"error": "Username or email already exists"}
        )

    @app.post("/api/login")
    def login(data: LoginRequest):
        if db.verify_user(data.username, data.password):
            return {"message": "Login successful", "username": data.username}
        return JSONResponse(status_code=401, content={"error": "Invalid credentials"})

    @app.delete("/api/users/{username}")
    def delete_user(username: str, data: DeleteUserRequest):
        if not db.users_col.find_one({'username': username}):
            return JSONResponse(status_code=404, content={"error": "User not found"})
        if not db.delete_user(username, data.password):
            return JSONResponse(status_code=401, content={"error": "Invalid password"})
        return {"message": "User and associated chat data deleted successfully"}

    @app.get("/api/sessions")
    def handle_sessions_get(username: str | None = None):
        if not username:
            return JSONResponse(status_code=400, content={"error": "Username is required"})
        return db.get_user_chat_sessions(username)

    @app.post("/api/sessions", status_code=201)
    def handle_sessions_post(data: SessionRequest):
        session_id = db.create_chat_session(data.username, data.session_name)
        return {"message": "Session created successfully", "session_id": session_id}

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str):
        if db.delete_chat_session(session_id):
            return {"message": "Session deleted successfully"}
        return JSONResponse(status_code=404, content={"error": "Session not found"})

    @app.get("/api/sessions/{session_id}/messages")
    def get_messages(session_id: str):
        return db.get_chat_messages(session_id)

    @app.post("/api/sessions/{session_id}/messages", status_code=201)
    def save_message(session_id: str, data: MessageRequest):
        db.save_chat_message(session_id, data.username, data.message)
        return {"message": "Message saved successfully"}

    @app.post("/api/sessions/{session_id}/generate")
    def generate_bot_response(session_id: str, data: MessageRequest):
        try:
            started_at = time.perf_counter()
            logger.info("Starting response generation for session %s", session_id)
            db.save_chat_message(session_id, data.username, data.message)
            bot_response = get_agent().generate_bot_response(session_id, data.message)
            if not bot_response:
                return JSONResponse(
                    status_code=500, content={"error": "Agent produced an empty response"}
                )
            db.save_chat_message(session_id, "assistant", bot_response)
            elapsed_seconds = time.perf_counter() - started_at
            record_rag_metrics(data.message, bot_response, elapsed_seconds)
            logger.info(
                "Completed response generation for session %s in %.2fs (%d characters)",
                session_id,
                elapsed_seconds,
                len(bot_response),
            )
            return {"bot_response": bot_response}
        except Exception as error:
            logger.exception("Error in /generate: %s", error)
            return JSONResponse(status_code=500, content={"error": f"Agent error: {error}"})

    @app.post("/api/sessions/{session_id}/generate-stream")
    def generate_bot_response_stream(session_id: str, data: MessageRequest):
        logger.info("Starting streaming response for session %s", session_id)
        db.save_chat_message(session_id, data.username, data.message)

        def generate_events():
            response_parts = []
            try:
                started_at = time.perf_counter()
                yield f"data: {json.dumps({'started': True})}\n\n"
                for content in get_agent().stream_bot_response(session_id, data.message):
                    response_parts.append(content)
                    yield f"data: {json.dumps({'content': content})}\n\n"
                bot_response = "".join(response_parts)
                if bot_response:
                    db.save_chat_message(session_id, "assistant", bot_response)
                    record_rag_metrics(
                        data.message,
                        bot_response,
                        time.perf_counter() - started_at,
                    )
                logger.info(
                    "Completed streaming response for session %s in %.2fs (%d chunks, %d characters)",
                    session_id,
                    time.perf_counter() - started_at,
                    len(response_parts),
                    len(bot_response),
                )
                yield f"data: {json.dumps({'done': True})}\n\n"
            except Exception as error:
                logger.exception("Error while streaming bot response")
                yield f"data: {json.dumps({'error': str(error)})}\n\n"

        return StreamingResponse(
            generate_events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "5000")),
    )