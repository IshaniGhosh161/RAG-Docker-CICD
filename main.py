import streamlit as st
import requests
import json
import logging
from dotenv import load_dotenv
import os
import logging_config

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Base URL for your API
API_BASE_URL = os.getenv('API_BASE_URL', 'http://localhost:5000')
mongo_uri = os.getenv('MONGO_URI')

class Authentication:
    def login_page(self):
        st.title("Chatbot Login")
        
        with st.form(key='login_form'):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            login_button = st.form_submit_button("Login")
            
            if login_button:
                try:
                    # API call for login
                    response = requests.post(f'{API_BASE_URL}/api/login', 
                                             json={'username': username, 'password': password})

                    if response.status_code == 200:
                        st.session_state['logged_in'] = True
                        st.session_state['username'] = username
                        st.session_state['current_session_id'] = None
                        st.session_state['delete_session_id'] = None
                        st.rerun()
                    else:
                        st.error("Invalid username or password")
                except requests.RequestException as e:
                    logger.exception("Login request failed")
                    st.error(f"Login failed: {str(e)}")
        
        st.write("Don't have an account?")
        if st.button("Register New Account"):
            st.session_state['show_registration'] = True
            st.rerun()

    def registration_page(self):
        st.title("Register New Account")
        
        with st.form(key='registration_form'):
            new_username = st.text_input("Choose a Username")
            email = st.text_input("Email Address")
            new_password = st.text_input("Choose a Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            
            register_button = st.form_submit_button("Create Account")
            
            if register_button:
                # Validation
                if not new_username:
                    st.error("Username is required")
                elif not email:
                    st.error("Email is required")
                elif not new_password:
                    st.error("Password is required")
                elif new_password != confirm_password:
                    st.error("Passwords do not match")
                else:
                    try:
                        # API call for registration
                        response = requests.post(f'{API_BASE_URL}/api/register', 
                                                 json={
                                                     'username': new_username, 
                                                     'password': new_password, 
                                                     'email': email
                                                 })
                        
                        if response.status_code == 201:
                            st.success("Account created successfully!")
                            st.session_state['show_registration'] = False
                            st.rerun()
                        else:
                            st.error(response.json().get('error', 'Registration failed'))
                    except requests.RequestException as e:
                        logger.exception("Registration request failed")
                        st.error(f"Registration failed: {str(e)}")
        
        if st.button("Back to Login"):
            st.session_state['show_registration'] = False
            st.rerun()

    def render(self):
        if st.session_state.get('show_registration', False):
            self.registration_page()
        else:
            self.login_page()

    def logout(self):
        st.session_state['logged_in'] = False
        st.session_state['username'] = None
        st.session_state['show_registration'] = False
        st.session_state['current_session_id'] = None
        st.session_state['delete_session_id'] = None

class ChatBot:
    def sidebar_chat_sessions(self):
        st.sidebar.title("Chat Sessions")
        
        # New Chat Button
        if st.sidebar.button("+ New Chat"):
            try:
                # API call to create new session
                response = requests.post(f'{API_BASE_URL}/api/sessions', 
                                         json={
                                             'username': st.session_state['username'], 
                                             'session_name': None
                                         })
                
                if response.status_code == 201:
                    new_session_id = response.json()['session_id']
                    st.session_state['current_session_id'] = new_session_id
                    st.rerun()
                else:
                    st.sidebar.error("Failed to create new session")
            except requests.RequestException as e:
                logger.exception("Session creation request failed")
                st.sidebar.error(f"Error creating session: {str(e)}")

        # Ensure delete confirmation state exists
        if 'delete_session_id' not in st.session_state:
            st.session_state['delete_session_id'] = None

        # Retrieve and display chat sessions
        try:
            response = requests.get(f'{API_BASE_URL}/api/sessions', 
                                    params={'username': st.session_state['username']})
            
            if response.status_code == 200:
                chat_sessions = response.json()
                
                for session in chat_sessions:
                    col1, col2 = st.sidebar.columns([3, 1])
                    
                    with col1:
                        if st.button(f"{session['session_name']}", key=f"session_{session['session_id']}"):
                            st.session_state['current_session_id'] = session['session_id']
                            st.rerun()
                    
                    with col2:
                        if st.button("🗑️", key=f"delete_{session['session_id']}"):
                            st.session_state['delete_session_id'] = session['session_id']
                            st.rerun()
            else:
                st.sidebar.error("Failed to retrieve sessions")
        except requests.RequestException as e:
            logger.exception("Session list request failed")
            st.sidebar.error(f"Error fetching sessions: {str(e)}")

        # Handle deletion confirmation
        if st.session_state['delete_session_id']:
            st.sidebar.warning(f"Confirm deletion of session?")
            
            col1, col2 = st.sidebar.columns(2)
            with col1:
                if st.button("Yes, Delete", key="confirm_delete"):
                    try:
                        # API call to delete session
                        response = requests.delete(f'{API_BASE_URL}/api/sessions/{st.session_state["delete_session_id"]}')
                        
                        if response.status_code == 200:
                            if st.session_state.get('current_session_id') == st.session_state['delete_session_id']:
                                st.session_state['current_session_id'] = None
                            st.session_state['delete_session_id'] = None
                            st.rerun()
                        else:
                            st.sidebar.error("Failed to delete session")
                    except requests.RequestException as e:
                        logger.exception("Session deletion request failed")
                        st.sidebar.error(f"Error deleting session: {str(e)}")
            
            with col2:
                if st.button("Cancel", key="cancel_delete"):
                    st.session_state['delete_session_id'] = None
                    st.rerun()

    def chat_interface(self):
        # Only proceed if a session is selected
        if 'current_session_id' not in st.session_state or st.session_state['current_session_id'] is None:
            st.info("Please select a session from the sidebar or click '+ New Chat' to start.")
            return

        # Retrieve and display previous messages
        try:
            response = requests.get(f'{API_BASE_URL}/api/sessions/{st.session_state["current_session_id"]}/messages')
            
            if response.status_code == 200:
                messages = response.json()
                
                # Display chat history
                for message in messages:
                    sender = message['username']
                    msg = message['message']
                    if sender == st.session_state['username']:
                        st.chat_message("user").write(msg)
                    else:
                        st.chat_message("assistant").write(msg)
            else:
                st.error("Failed to retrieve chat history")
        except requests.RequestException as e:
            logger.exception("Message history request failed")
            st.error(f"Error fetching messages: {str(e)}")

        # Chat input
        if prompt := st.chat_input("Your message"):
            # Save user message
            st.chat_message("user").write(prompt)
            
            # Create an assistant chat message container
            response_placeholder = st.empty()

            try:
                # The generate endpoint persists the user message before processing it.
                response = requests.post(
                    f'{API_BASE_URL}/api/sessions/{st.session_state["current_session_id"]}/generate-stream',
                    json={
                        'username': st.session_state['username'],
                        'message': prompt,
                    },
                    stream=True,
                    timeout=None,
                )

                if response.status_code == 200:
                    bot_response = ''
                    for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                        if not line or not line.startswith('data: '):
                            continue

                        event = json.loads(line[6:])
                        if 'error' in event:
                            response_placeholder.error(event['error'])
                            break
                        if 'content' in event:
                            bot_response += event['content']
                            response_placeholder.markdown(bot_response)
                else:
                    error_detail = response.json().get('error', 'Unknown backend error')
                    response_placeholder.error(f"Error ({response.status_code}): {error_detail}")
            except requests.RequestException as e:
                logger.exception("Streaming generation request failed")
                response_placeholder.error(f"Error generating response: {str(e)}")