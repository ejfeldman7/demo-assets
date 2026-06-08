# Simple Databricks Chat Example (Streamlit)

A minimal, production-ready example of integrating Databricks Model Serving endpoints (Multi-Agent Supervisor/MAS) with Streamlit.

## Features

- 🤖 **Beautiful Chat Interface**: Built with Streamlit's native chat components
- 🔐 **OAuth Authentication**: Uses Databricks on-behalf-of authentication
- 💬 **Conversation History**: Maintains context across messages
- 📦 **Easy Deployment**: Deploy with Databricks Asset Bundles (DABs)
- 🎯 **Minimal Code**: Just ~100 lines of Python!

## Why Streamlit?

Streamlit makes building chat apps incredibly simple:
- ✅ Built-in chat UI components (`st.chat_message`, `st.chat_input`)
- ✅ No HTML/CSS/JavaScript required
- ✅ Automatic session state management
- ✅ Real-time updates and interactivity
- ✅ Professional look out of the box

## Prerequisites

1. **Databricks Workspace**: Access to a Databricks workspace
2. **Model Serving Endpoint**: A deployed MAS (Multi-Agent Supervisor) endpoint
3. **Databricks CLI**: Install with `pip install databricks-cli`

## Quick Start

### 1. Clone and Configure

```bash
# Navigate to the project
cd /path/to/simple_chat

# Update app.yaml with your endpoint name
# Edit line 4: MAS_ENDPOINT_NAME value

# Update databricks.yml
# - Replace "your-mas-endpoint-name" with your endpoint name (2 places)
# - Replace "your-workspace.cloud.databricks.com" with your workspace URL
```

### 2. Deploy with Databricks Asset Bundles

```bash
# Authenticate with Databricks
databricks auth login --host https://your-workspace.cloud.databricks.com

# Deploy the app
databricks bundle deploy

# Or deploy and start in one command
databricks apps deploy simple-chat-example --source-code-path .
```

### 3. Access Your App

After deployment, you'll receive a URL like:
```
https://simple-chat-example-xxxxx.aws.databricksapps.com
```

Open this URL in your browser to start chatting!

## Project Structure

```
simple_chat/
├── app.py              # Main Streamlit application (all UI + logic)
├── mas_service.py      # MAS endpoint communication
├── requirements.txt    # Python dependencies
├── app.yaml           # Databricks App configuration
├── databricks.yml     # DABs configuration
└── README.md          # This file
```

**That's it!** No templates, no static files, no complex routing. Just clean Python.

## How It Works

### Architecture

```
User Browser → Streamlit App → MAS Endpoint → AI Response → User Browser
                    ↓
             OAuth Token (on-behalf-of)
```

### Key Components

#### 1. **Streamlit App (`app.py`)**
- Single file with all UI and logic
- Uses `st.chat_message()` for messages
- Uses `st.chat_input()` for user input
- Manages conversation in `st.session_state`

#### 2. **MAS Service (`mas_service.py`)**
- Converts messages to ResponsesAgent format
- Queries Databricks Model Serving endpoint
- Extracts and formats responses

### Code Walkthrough

```python
# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Get user input
if prompt := st.chat_input("Type your message..."):
    # Process and display response
    with st.chat_message("assistant"):
        response = query_mas_endpoint(...)
        st.markdown(response)
```

That's it! Streamlit handles all the complexity.

## Configuration

### Environment Variables

Set in `app.yaml`:

- `MAS_ENDPOINT_NAME`: Your Model Serving endpoint name

### Databricks Resources

Defined in `databricks.yml`:

- **User API Scopes**: `serving.serving-endpoints` for endpoint access
- **Permissions**: `CAN_USE` for all users
- **Resources**: Link to your MAS endpoint with `CAN_QUERY` permission

## OAuth Authentication

This app uses **on-behalf-of (OBO) authentication**, meaning:

- The app runs queries as the logged-in user
- Users must have access to the Model Serving endpoint
- Authentication is automatic via Databricks Apps

No manual token management required!

## Customization

### Change the Endpoint

Update these files:
1. `app.yaml` - line 4
2. `databricks.yml` - line 25
3. Optionally update `MAS_ENDPOINT_NAME` default in `app.py`

### Customize the UI

Edit `app.py`:
- **Colors/Theme**: Add CSS in `st.markdown()` with `unsafe_allow_html=True`
- **Welcome Message**: Update the first message in `st.session_state.messages`
- **Sidebar**: Modify the `with st.sidebar:` section
- **Page Config**: Update `st.set_page_config()` for icon, title, layout

### Add Features

Streamlit makes it easy to add:

**User avatars:**
```python
with st.chat_message("user", avatar="👤"):
    st.markdown(message)
```

**Message timestamps:**
```python
st.caption(f"Sent at {timestamp}")
```

**Code highlighting:**
```python
if "```" in response:
    st.code(code_block, language="python")
```

**Export chat:**
```python
st.download_button("Download Chat", chat_history)
```

## Deployment Options

### Option 1: Databricks Asset Bundles (Recommended)

```bash
databricks bundle deploy
```

### Option 2: Direct App Deployment

```bash
databricks apps deploy simple-chat-example --source-code-path .
```

### Option 3: Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"
export DATABRICKS_TOKEN="your-token"
export MAS_ENDPOINT_NAME="your-endpoint-name"

# Run locally
streamlit run app.py
```

Visit http://localhost:8501

## Troubleshooting

### Streamlit Issues

**White screen or "Please wait..."**
- Check that `app.py` doesn't have syntax errors
- View browser console for JavaScript errors
- Check app logs in Databricks workspace

**Session state not persisting**
- This is normal in Databricks Apps (stateless)
- Consider adding database storage for persistence

### Endpoint Issues

**Error: "Endpoint not found"**
- Verify `MAS_ENDPOINT_NAME` matches your deployed endpoint
- Check endpoint permissions in Databricks workspace

**Error: "Authentication failed"**
- Ensure user has access to the Model Serving endpoint
- Verify `user_api_scopes` includes `serving.serving-endpoints`

### Deployment fails

- Check Databricks CLI authentication: `databricks auth token`
- Verify workspace URL in `databricks.yml`
- Ensure endpoint exists and is running

## Best Practices

1. **Streamlit-Specific**
   - Use `st.session_state` for all stateful data
   - Add `st.spinner()` for long operations
   - Use `st.cache_data` for expensive computations
   - Add error handling with `try/except` and `st.error()`

2. **Performance**
   - Keep conversation history reasonable (< 20 messages)
   - Use `st.experimental_fragment` for partial updates
   - Add loading indicators with `st.spinner()`

3. **User Experience**
   - Show clear error messages with `st.error()`
   - Add helpful sidebar information
   - Include example queries
   - Provide a "Clear Chat" button

## Streamlit Tips & Tricks

### Add Emoji Reactions

```python
reaction = st.feedback("thumbs")
if reaction:
    st.toast(f"Thanks for the feedback!")
```

### Add File Upload

```python
uploaded_file = st.file_uploader("Upload a file")
if uploaded_file:
    content = uploaded_file.read()
    # Process file...
```

### Add Tabs

```python
tab1, tab2 = st.tabs(["Chat", "Settings"])
with tab1:
    # Chat interface
with tab2:
    # Settings
```

### Add Metrics

```python
col1, col2, col3 = st.columns(3)
col1.metric("Messages", len(st.session_state.messages))
col2.metric("Tokens", total_tokens)
col3.metric("Cost", f"${cost:.4f}")
```

## Learn More

- [Streamlit Documentation](https://docs.streamlit.io/)
- [Streamlit Chat Elements](https://docs.streamlit.io/library/api-reference/chat)
- [Databricks Apps Documentation](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/)
- [Model Serving Documentation](https://docs.databricks.com/machine-learning/model-serving/)

## Comparison: Flask vs Streamlit

| Feature | Flask | Streamlit |
|---------|-------|-----------|
| **Lines of Code** | ~200 | ~100 |
| **HTML/CSS/JS** | Required | Not needed |
| **Chat Components** | Build from scratch | Built-in |
| **State Management** | Manual | Automatic |
| **UI Updates** | Manual AJAX | Automatic |
| **Learning Curve** | Moderate | Easy |
| **Best For** | Custom UIs | Data apps & prototypes |

**Winner**: Streamlit for simplicity! ✨

## License

This is an example application for educational purposes. Modify and use as needed!

## Support

For issues with:
- **This code**: Check the troubleshooting section above
- **Streamlit**: Visit [Streamlit Community](https://discuss.streamlit.io/)
- **Databricks Apps**: Refer to Databricks documentation
- **Model Serving**: Check endpoint logs in your workspace

---

**Happy chatting with Streamlit! 🎈**
