"""
Data Loader Module
Handles Unity Catalog Volume interaction and file operations.
"""

import os
import tempfile
from typing import List, Optional, Dict
from databricks.sdk import WorkspaceClient
from pypdf import PdfReader


class DataLoader:
    """Handles loading and extracting data from Unity Catalog Volumes."""
    
    def __init__(self, workspace_client: Optional[WorkspaceClient] = None):
        """
        Initialize DataLoader with a Databricks WorkspaceClient.
        
        Args:
            workspace_client: Optional WorkspaceClient. If None, creates one automatically
                            using the app's service principal credentials.
        """
        self.client = workspace_client or WorkspaceClient()
    
    def list_catalogs(self) -> List[str]:
        """
        List all catalogs available in Unity Catalog.
        
        Returns:
            List of catalog names.
        """
        try:
            catalogs = list(self.client.catalogs.list())
            return [catalog.name for catalog in catalogs if catalog.name]
        except Exception as e:
            print(f"Error listing catalogs: {e}")
            return []
    
    def list_schemas(self, catalog_name: str) -> List[str]:
        """
        List all schemas in a given catalog.
        
        Args:
            catalog_name: Name of the catalog.
            
        Returns:
            List of schema names.
        """
        try:
            schemas = list(self.client.schemas.list(catalog_name=catalog_name))
            return [schema.name for schema in schemas if schema.name]
        except Exception as e:
            print(f"Error listing schemas: {e}")
            return []
    
    def list_volumes(self, catalog_name: str, schema_name: str) -> List[str]:
        """
        List all volumes in a given catalog and schema.
        
        Args:
            catalog_name: Name of the catalog.
            schema_name: Name of the schema.
            
        Returns:
            List of volume names.
        """
        try:
            volumes = list(self.client.volumes.list(
                catalog_name=catalog_name,
                schema_name=schema_name
            ))
            return [volume.name for volume in volumes if volume.name]
        except Exception as e:
            print(f"Error listing volumes: {e}")
            return []
    
    def list_files(self, catalog_name: str, schema_name: str, volume_name: str) -> List[Dict[str, str]]:
        """
        List all files in a Unity Catalog Volume.
        
        Args:
            catalog_name: Name of the catalog.
            schema_name: Name of the schema.
            volume_name: Name of the volume.
            
        Returns:
            List of dictionaries containing file information (name, path, extension).
        """
        try:
            volume_path = f"/Volumes/{catalog_name}/{schema_name}/{volume_name}"
            files = list(self.client.files.list_directory_contents(directory_path=volume_path))
            
            file_list = []
            for file in files:
                if not file.is_directory:
                    file_name = file.name
                    file_ext = os.path.splitext(file_name)[1].lower()
                    
                    # Filter for supported file types
                    if file_ext in ['.pdf', '.txt', '.md']:
                        file_list.append({
                            'name': file_name,
                            'path': file.path,
                            'extension': file_ext
                        })
            
            return file_list
        except Exception as e:
            print(f"Error listing files: {e}")
            return []
    
    def download_file(self, file_path: str) -> Optional[str]:
        """
        Download a file from Unity Catalog Volume to local temporary storage.
        
        Args:
            file_path: Full path to the file in Unity Catalog (e.g., /Volumes/catalog/schema/volume/file.pdf).
            
        Returns:
            Path to the downloaded local file, or None if download failed.
        """
        try:
            # Create a temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_path)[1])
            temp_file_path = temp_file.name
            temp_file.close()
            
            # Download the file
            with open(temp_file_path, 'wb') as f:
                content = self.client.files.download(file_path)
                f.write(content.contents.read())
            
            return temp_file_path
        except Exception as e:
            print(f"Error downloading file: {e}")
            return None
    
    def extract_text_from_pdf(self, file_path: str, max_pages: int = 20) -> str:
        """
        Extract text from a PDF file.
        
        Args:
            file_path: Path to the local PDF file.
            max_pages: Maximum number of pages to extract (default: 20).
            
        Returns:
            Extracted text from the PDF.
        """
        try:
            reader = PdfReader(file_path)
            text_parts = []
            
            # Limit to max_pages to prevent memory issues
            num_pages = min(len(reader.pages), max_pages)
            
            for page_num in range(num_pages):
                page = reader.pages[page_num]
                text_parts.append(page.extract_text())
            
            return "\n".join(text_parts)
        except Exception as e:
            print(f"Error extracting text from PDF: {e}")
            return ""
    
    def extract_text_from_file(self, file_path: str, file_extension: str) -> str:
        """
        Extract text from a file based on its extension.
        
        Args:
            file_path: Path to the local file.
            file_extension: File extension (e.g., '.pdf', '.txt', '.md').
            
        Returns:
            Extracted text from the file.
        """
        try:
            if file_extension == '.pdf':
                return self.extract_text_from_pdf(file_path)
            elif file_extension in ['.txt', '.md']:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            else:
                return f"Unsupported file type: {file_extension}"
        except Exception as e:
            print(f"Error extracting text: {e}")
            return ""
    
    def cleanup_temp_file(self, file_path: str):
        """
        Delete a temporary file.
        
        Args:
            file_path: Path to the temporary file to delete.
        """
        try:
            if file_path and os.path.exists(file_path):
                os.unlink(file_path)
        except Exception as e:
            print(f"Error cleaning up temp file: {e}")



