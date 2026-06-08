"""
Simple Databricks Chat Application with Streamlit

A minimal example of integrating Databricks Model Serving endpoints
with Streamlit using OAuth authentication.
"""
import os
import logging
import streamlit as st
from databricks.sdk.core import Config
from mas_service import query_mas_endpoint

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Get MAS endpoint name from environment
MAS_ENDPOINT_NAME = os.getenv("MAS_ENDPOINT_NAME", "your-endpoint-name")

# Page configuration
st.set_page_config(
    page_title="Databricks AI Chat",
    page_icon="🤖",
    layout="centered"
)

# Custom CSS for better styling
st.markdown("""
    <style>
    .stChatMessage {
        padding: 1rem;
        border-radius: 0.5rem;
    }
    .main {
        max-width: 800px;
    }
    </style>
""", unsafe_allow_html=True)

# App title
st.title("🤖 Databricks AI Chat")
st.caption("Powered by Databricks Model Serving")

# Initialize chat history in session state
if "messages" not in st.session_state:
    st.session_state.messages = []
    # Add welcome message
    st.session_state.messages.append({
        "role": "assistant",
        "content": "Hello! I'm your AI Assistant powered by Databricks. Ask me anything!"
    })

# Display chat messages
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Chat input
if prompt := st.chat_input("Type your message..."):
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    # Display user message
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # Get assistant response
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                # Set up on-behalf-of authentication using user's token
                user_access_token = st.context.headers.get('X-Forwarded-Access-Token')
                if user_access_token:
                    cfg = Config()
                    os.environ["DATABRICKS_HOST"] = cfg.host
                    os.environ["DATABRICKS_AUTH_TYPE"] = "pat"
                    os.environ["DATABRICKS_TOKEN"] = user_access_token
                    logger.info(f"Using OBO authentication for user")
                
                logger.info(f"Querying endpoint: {MAS_ENDPOINT_NAME}")
                
                # Build messages list for context
                messages = [
                    {"role": msg["role"], "content": msg["content"]}
                    for msg in st.session_state.messages[:-1]  # Exclude the current user message
                ]
                messages.append({"role": "user", "content": prompt})
                
                # Query the MAS endpoint
                response_text, request_id = query_mas_endpoint(
                    endpoint_name=MAS_ENDPOINT_NAME,
                    messages=messages
                )
                
                # Display response
                st.markdown(response_text)
                
                # Add to chat history
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": response_text
                })
                
                # Show request ID in expander
                if request_id:
                    with st.expander("ℹ️ Request Details"):
                        st.code(f"Request ID: {request_id}")
                
            except Exception as e:
                error_msg = f"Error: {str(e)}"
                st.error(error_msg)
                logger.error(f"Error processing chat request: {str(e)}")
                
                # Add error to chat history
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": error_msg
                })

# Sidebar with info
with st.sidebar:
    st.header("About")
    st.markdown("""
    This is a simple chat application powered by:
    - **Streamlit** for the UI
    - **Databricks Model Serving** for AI responses
    - **OAuth** for secure authentication
    
    ---
    
    ### How to Use
    1. Type your question in the chat input
    2. Press Enter to send
    3. Wait for the AI response
    
    ---
    
    ### Features
    - 💬 Conversation history
    - 🔐 Secure OAuth authentication  
    - 🎯 Simple and elegant UI
    """)
    
    # Clear chat button
    if st.button("🗑️ Clear Chat History"):
        st.session_state.messages = [{
            "role": "assistant",
            "content": "Hello! I'm your AI Assistant powered by Databricks. Ask me anything!"
        }]
        st.rerun()
