"""
Image service module for Unity Catalog Image Gallery.

Handles image loading from Unity Catalog volumes using the Databricks SDK.
"""

from typing import Optional
from io import BytesIO
import streamlit as st
from PIL import Image

from .database import db_manager
# Image service for Unity Catalog volume operations


class ImageService:
    """Service for loading images from Unity Catalog volumes."""
    
    def __init__(self):
        self.workspace_client = db_manager.workspace_client
    
    
    def load_image_from_volume(self, file_path: str) -> Optional[Image.Image]:
        """Load image from Unity Catalog volume using Databricks SDK."""
        try:
            # Ensure file_path is a string and not None/empty
            if not file_path:
                st.warning("⚠️ Empty file path provided")
                return None
                
            # Convert to string if it isn't already (handles integer IDs from database)
            file_path_str = str(file_path).strip()
            
            
            # Normalize file path - remove dbfs: prefix if present 
            # WorkspaceClient expects just /Volumes/... format
            if file_path_str.startswith('dbfs:/Volumes/'):
                normalized_path = file_path_str.replace('dbfs:', '')
            elif file_path_str.startswith('/Volumes/'):
                normalized_path = file_path_str
            else:
                # Try to handle other common formats
                if file_path_str.isdigit():
                    st.warning(f"⚠️ Path appears to be numeric ID: {file_path_str}")
                    return None
                elif '/' not in file_path_str:
                    # Check if it's just a filename - try to construct path
                    if '.' in file_path_str and any(file_path_str.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']):
                        # It's likely an image file, try to construct the full path
                        from .config import VOLUME_BASE_PATH
                        potential_path = f"{VOLUME_BASE_PATH}/{file_path_str}"
                        st.info(f"🔧 Attempting to construct path from filename: `{potential_path}`")
                        normalized_path = potential_path
                    else:
                        st.warning(f"⚠️ Path appears to be just filename without extension: {file_path_str}")
                        return None
                elif file_path_str.startswith('/'):
                    # Might be a filesystem path, check if it needs /Volumes prefix
                    if '/Volumes/' not in file_path_str:
                        st.warning(f"⚠️ Filesystem path doesn't contain /Volumes/: {file_path_str}")
                        return None
                    else:
                        # Extract the /Volumes/ part
                        volumes_index = file_path_str.find('/Volumes/')
                        normalized_path = file_path_str[volumes_index:]
                        st.info(f"🔧 Extracted volume path: `{normalized_path}`")
                else:
                    st.warning(f"⚠️ Invalid volume path format: {file_path_str}")
                    st.info("Expected formats: '/Volumes/catalog/schema/volume/file' or 'dbfs:/Volumes/catalog/schema/volume/file'")
                    return None
            
            # Use Databricks SDK to download file
            
            # Download the file - try different methods to get content
            download_response = self.workspace_client.files.download(normalized_path)
            
            # Handle the streaming response to get file content
            try:
                # download_response.contents returns a StreamingResponse - read from it
                file_data = download_response.contents.read()
            except AttributeError:
                try:
                    # Try alternative approaches
                    file_data = download_response.content
                except AttributeError:
                    try:
                        file_data = download_response.read()
                    except (AttributeError, TypeError):
                        try:
                            # Maybe it's already bytes
                            file_data = download_response.contents
                        except Exception:
                            # Last resort - the response itself
                            file_data = download_response
            
            # Convert to PIL Image
            return Image.open(BytesIO(file_data))
                
        except Exception as e:
            st.error(f"❌ Error loading image {file_path}: {str(e)}")
            st.info(f"Error type: {type(e).__name__}")
            return None

    def validate_image_path(self, file_path: str) -> bool:
        """Validate that the file path is a valid Unity Catalog volume path."""
        if not file_path or not isinstance(file_path, str):
            return False
        
        volume_path = file_path.strip()
        return volume_path.startswith('/Volumes/') or volume_path.startswith('dbfs:/Volumes/')


# Global image service instance
image_service = ImageService()


# Convenience functions
def load_image_from_volume(file_path: str) -> Optional[Image.Image]:
    """Load image from Unity Catalog volume using Databricks SDK."""
    return image_service.load_image_from_volume(file_path)
