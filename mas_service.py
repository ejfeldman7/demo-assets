"""
Multi-Agent Supervisor (MAS) Endpoint Service

Handles communication with Databricks Model Serving endpoints
using the ResponsesAgent format.
"""
import logging
from typing import List, Dict, Tuple, Optional
from mlflow.deployments import get_deploy_client

logger = logging.getLogger(__name__)


def _convert_to_responses_format(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Convert standard message format to ResponsesAgent format.
    
    Args:
        messages: List of dicts with 'role' and 'content' keys
    
    Returns:
        List of dicts in ResponsesAgent format for agent/v1/responses endpoints
    """
    formatted_messages = []
    
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        if role == "user":
            # User messages are simple
            formatted_msg = {
                "role": "user",
                "content": content
            }
        else:
            # Assistant messages use structured format
            formatted_msg = {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content}]
            }
        
        formatted_messages.append(formatted_msg)
    
    return formatted_messages


def query_mas_endpoint(endpoint_name: str, messages: List[Dict[str, str]]) -> Tuple[str, Optional[str]]:
    """
    Query the Multi-Agent Supervisor endpoint.
    
    Args:
        endpoint_name: Name of the MAS serving endpoint
        messages: List of message dicts with 'role' and 'content' keys
    
    Returns:
        tuple: (response_text, request_id)
    """
    try:
        logger.info(f"Querying MAS endpoint: {endpoint_name}")
        client = get_deploy_client("databricks")

        # Convert messages to ResponsesAgent format
        input_messages = _convert_to_responses_format(messages)

        # Prepare input payload
        inputs = {
            "input": input_messages,
            "context": {},
            "databricks_options": {"return_trace": True}
        }

        # Make the prediction call
        response = client.predict(endpoint=endpoint_name, inputs=inputs)

        # Extract request ID
        request_id = response.get("databricks_output", {}).get("databricks_request_id")

        # Extract response text from output
        response_text = ""
        output_items = response.get("output", [])

        for item in output_items:
            item_type = item.get("type")

            if item_type == "message":
                # Extract text from message content
                content_items = item.get("content", [])
                
                for content_item in content_items:
                    # ResponsesAgent uses "output_text" type
                    if content_item.get("type") == "output_text":
                        text = content_item.get("text", "")
                        if text:
                            response_text += text

        if not response_text:
            logger.warning("No response text found in endpoint output")
            response_text = "I received your message but couldn't generate a response."

        logger.info(f"MAS response received (request_id: {request_id})")
        return response_text, request_id

    except Exception as e:
        logger.error(f"Error querying MAS endpoint: {str(e)}")
        raise

