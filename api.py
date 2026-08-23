import os
import sys
import json
import traceback
import logging
import time
from functools import lru_cache
from flask import Flask, request, jsonify, make_response, Response, stream_with_context
from flask_cors import CORS
from database import DatabaseManager
import logging_config
from config import FLASK_DEBUG

logger = logging.getLogger(__name__)

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

db = DatabaseManager()

@lru_cache(maxsize=1)
def get_agent():
    from script.agent import Agent

    return Agent("shared")

def create_app():
    app = Flask(__name__)
    # CORS handles OPTIONS requests automatically when properly configured
    CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

    # Root Documentation Route
    @app.route('/api/health', methods=['GET'])
    def health_check():
        return jsonify({"status": "healthy", "message": "Chatbot API Running"}), 200

    # Register Route
    @app.route('/api/register', methods=['POST'])
    def register():
        data = request.get_json() or {}

        if not all(k in data for k in ('username', 'password', 'email')):
            return jsonify({"error": "Missing required fields"}), 400

        if db.register_user(data['username'], data['password'], data['email']):
            return jsonify({"message": "User registered successfully"}), 201
        else:
            return jsonify({"error": "Username or email already exists"}), 409

    # Login Route
    @app.route('/api/login', methods=['POST'])
    def login():
        data = request.get_json() or {}
        
        if not all(k in data for k in ('username', 'password')):
            return jsonify({"error": "Missing username or password"}), 400
        
        if db.verify_user(data['username'], data['password']):
            return jsonify({
                "message": "Login successful", 
                "username": data['username']
            }), 200
        else:
            return jsonify({"error": "Invalid credentials"}), 401

    # Sessions Route - GET
    @app.route('/api/sessions', methods=['GET'])
    def handle_sessions_get():
        username = request.args.get('username')
        
        if not username:
            return jsonify({"error": "Username is required"}), 400
        
        sessions = db.get_user_chat_sessions(username)
        return jsonify(sessions), 200

    # Sessions Route - POST
    @app.route('/api/sessions', methods=['POST'])
    def handle_sessions_post():
        data = request.get_json() or {}
        username = data.get('username')

        if not username:
            return jsonify({"error": "Username is required"}), 400

        session_name = data.get('session_name', None)
        session_id = db.create_chat_session(username, session_name)
        
        return jsonify({"message": "Session created successfully", "session_id": session_id}), 201

    # Delete Session Route
    @app.route('/api/sessions/<session_id>', methods=['DELETE'])
    def delete_session(session_id):
        success = db.delete_chat_session(session_id)
        if success:
            return jsonify({"message": "Session deleted successfully"}), 200
        else:
            return jsonify({"error": "Session not found"}), 404

    # Get Messages
    @app.route('/api/sessions/<session_id>/messages', methods=['GET'])
    def get_messages(session_id):
        messages = db.get_chat_messages(session_id)
        return jsonify(messages), 200

    # Save Message
    @app.route('/api/sessions/<session_id>/messages', methods=['POST'])
    def save_message(session_id):
        data = request.get_json() or {}

        if 'username' not in data or 'message' not in data:
            return jsonify({"error": "Missing username or message field"}), 400
        
        db.save_chat_message(session_id, data['username'], data['message'])
        return jsonify({"message": "Message saved successfully"}), 201

    # Generate and Save Bot Response
    @app.route('/api/sessions/<session_id>/generate', methods=['POST'])
    def generate_bot_response(session_id):
        data = request.get_json() or {}

        if 'message' not in data or 'username' not in data:
            return jsonify({"error": "Missing required fields"}), 400

        user_message = data['message']
        username = data['username']
        
        try:
            started_at = time.perf_counter()
            logger.info("Starting response generation for session %s", session_id)
            db.save_chat_message(session_id, username, user_message)
            agent = get_agent()
            
            # Generate response
            bot_response = agent.generate_bot_response(session_id, user_message)
            
            if bot_response:
                # Save message to DB as assistant/bot
                db.save_chat_message(session_id, "assistant", bot_response)
                logger.info(
                    "Completed response generation for session %s in %.2fs (%d characters)",
                    session_id,
                    time.perf_counter() - started_at,
                    len(bot_response),
                )
                # Return 200 or 201 OK payload
                return jsonify({"bot_response": bot_response}), 200
            else:
                return jsonify({"error": "Agent produced an empty response"}), 500
                
        except Exception as e:
            # Print full exception stack trace in the terminal for debugging
            logger.exception("Error in /generate: %s", e)
            return jsonify({"error": f"Agent error: {str(e)}"}), 500

    @app.route('/api/sessions/<session_id>/generate-stream', methods=['POST'])
    def generate_bot_response_stream(session_id):
        data = request.get_json() or {}

        if 'message' not in data or 'username' not in data:
            return jsonify({"error": "Missing required fields"}), 400

        user_message = data['message']
        username = data['username']
        logger.info("Starting streaming response for session %s", session_id)
        db.save_chat_message(session_id, username, user_message)

        def generate_events():
            response_parts = []
            try:
                started_at = time.perf_counter()
                # Added .encode('utf-8') to all text outputs
                yield f"data: {json.dumps({'started': True})}\n\n".encode('utf-8')

                for content in get_agent().stream_bot_response(session_id, user_message):
                    response_parts.append(content)
                    yield f"data: {json.dumps({'content': content})}\n\n".encode('utf-8')

                bot_response = ''.join(response_parts)
                if bot_response:
                    db.save_chat_message(session_id, "assistant", bot_response)
                logger.info(
                    "Completed streaming response for session %s in %.2fs (%d chunks, %d characters)",
                    session_id,
                    time.perf_counter() - started_at,
                    len(response_parts),
                    len(bot_response),
                )

                yield f"data: {json.dumps({'done': True})}\n\n".encode('utf-8')
            except Exception as error:
                logger.exception("Error while streaming bot response")
                yield f"data: {json.dumps({'error': str(error)})}\n\n".encode('utf-8')

        return Response(
            stream_with_context(generate_events()),
            mimetype='text/event-stream; charset=utf-8',
            direct_passthrough=True,
            headers={
                'Cache-Control': 'no-cache, no-transform',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            },
        )

    # Error Handlers
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Endpoint not found"}), 404

    @app.errorhandler(500)
    def server_error(error):
        return jsonify({"error": "Internal server error"}), 500

    return app

app = create_app()

def run_server():
    debug = FLASK_DEBUG
    app.run(
        debug=debug,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "5000")),
        use_reloader=False,
        threaded=True,
    )

if __name__ == '__main__':
    run_server()