from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import os
import hashlib
from datetime import datetime
import uuid
import logging
from dotenv import load_dotenv

import logging_config

logger = logging.getLogger(__name__)

load_dotenv()
mongo_uri = os.getenv('MONGO_URI')

class DatabaseManager:
    def __init__(self, mongo_uri=mongo_uri, db_name="chatbot"):
        # Initialize MongoDB connection
        self.client = MongoClient(mongo_uri)
        self.db = self.client[db_name]
        
        # MongoDB collections
        self.users_col = self.db["users"]
        self.sessions_col = self.db["sessions"]
        self.messages_col = self.db["messages"]

        self.sessions_col.create_index([("username", 1), ("created_at", -1)])
        self.sessions_col.create_index("session_id", unique=True)
        self.messages_col.create_index("session_id")
        logger.info("Connected to MongoDB database '%s'", db_name)

    def hash_password(self, password):
        return hashlib.sha256(password.encode()).hexdigest()

    def register_user(self, username, password, email):
        # Check if username or email already exists in MongoDB
        if self.users_col.find_one({"$or": [{"username": username}, {"email": email}]}):
            return False
        
        # Create new user
        hashed_password = self.hash_password(password)
        user_data = {
            'username': username,
            'password': hashed_password,
            'email': email
        }
        
        try:
            self.users_col.insert_one(user_data)
            logger.info("Registered user '%s'", username)
            return True
        except DuplicateKeyError:
            logger.warning("Registration rejected due to duplicate username or email")
            return False

    def verify_user(self, username, password):
        user = self.users_col.find_one({"username": username})
        if user and user['password'] == self.hash_password(password):
            logger.info("Successful login for user '%s'", username)
            return True
        logger.warning("Failed login attempt for user '%s'", username)
        return False

    def create_chat_session(self, username, session_name=None):
        # Generate unique session ID
        session_id = str(uuid.uuid4())
        
        # Default session name if none provided
        if not session_name:
            session_name = f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        session_data = {
            'username': username,
            'session_id': session_id,
            'session_name': session_name,
            'created_at': datetime.now().isoformat()
        }
        
        self.sessions_col.insert_one(session_data)
        logger.info("Created chat session %s for user '%s'", session_id, username)
        return session_id

    def get_user_chat_sessions(self, username):
        sessions = self.sessions_col.find(
            {"username": username},
            {"_id": 0, "session_id": 1, "session_name": 1, "created_at": 1}
        ).sort("created_at", -1)
        user_sessions = [
            {
                "session_id": session['session_id'],
                "session_name": session['session_name'],
                "created_at": session['created_at']
            } for session in sessions
        ]
        logger.info("Loaded %d chat sessions for user '%s'", len(user_sessions), username)
        return user_sessions

    def save_chat_message(self, session_id, sender, message):
        message_data = {
            'session_id': session_id,
            'username': sender,
            'message': message,
            'timestamp': datetime.now().isoformat()
        }
        
        self.messages_col.insert_one(message_data)
        logger.info("Saved %s message for session %s", sender, session_id)

    def get_chat_messages(self, session_id):
        messages = list(self.messages_col.find({"session_id": session_id},{"_id": 0, "username": 1, "message": 1, "timestamp": 1}))
        logger.info("Loaded %d messages for session %s", len(messages), session_id)
        return messages

    def delete_chat_session(self, session_id):
        # Delete session and associated messages
        result = self.sessions_col.delete_one({"session_id": session_id})
        if result.deleted_count == 0:
            logger.warning("Chat session %s was not found for deletion", session_id)
            return False

        self.messages_col.delete_many({"session_id": session_id})
        logger.info("Deleted chat session %s and its messages", session_id)
        return True