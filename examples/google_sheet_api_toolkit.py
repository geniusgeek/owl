# google_sheets_api_toolkit.py

import os
import re
import io
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse, unquote

import pandas as pd
from camel.logger import get_logger
from camel.toolkits.base import BaseToolkit
from camel.toolkits.function_tools import FunctionTool
from camel.utils import MCPServer, api_keys_required # api_keys_required for checking env vars

# requirements: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib pandas openpyxl

# Google API Client Libraries
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.http import MediaIoBaseDownload # For downloading from Drive as Excel

logger = get_logger(__name__)

SCOPES_SHEETS = ['https://www.googleapis.com/auth/spreadsheets.readonly']
SCOPES_DRIVE = ['https://www.googleapis.com/auth/drive.readonly'] # If using Drive API to export

try:
    import openpyxl
except ImportError:
    logger.warning("openpyxl library not found. Needed for .xlsx. pip install openpyxl")


class TimeoutHttp(Http): # Helper from previous example for timeouts
    def __init__(self, timeout=20, *args, **kwargs): # Increased default timeout
        self.timeout = timeout
        super().__init__(*args, **kwargs)
    # ... (request method - include if needed, or rely on googleapiclient defaults)
    def request(self, uri, method="GET", body=None, headers=None, redirections=5, connection_type=None):
        import socket # Import moved inside for lazy loading if not used often
        default_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(self.timeout)
        try:
            response, content = super().request(uri, method, body, headers, redirections, connection_type)
        finally:
            socket.setdefaulttimeout(default_timeout)
        return response, content

@MCPServer()
class GoogleSheetsAPIToolkit(BaseToolkit):
    r"""A toolkit for reading data from Google Sheets using the Google Sheets API.

    It attempts to authenticate using OAuth 2.0 credentials (client ID, secret,
    refresh token) provided via environment variables. If authentication fails
    or is not configured, it provides guidance.
    """

    def __init__(self, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)
        self.creds = None
        self.sheets_service = None
        self.drive_service = None # Optional, for exporting as .xlsx
        self._initialize_services()

    def _initialize_services(self):
        try:
            self.creds = self._authenticate()
            if self.creds:
                # Using custom Http with timeout for robustness
                http_client = TimeoutHttp() if self.timeout else None # Or standard Http
                self.sheets_service = build('sheets', 'v4', credentials=self.creds, http=http_client)
                # self.drive_service = build('drive', 'v3', credentials=self.creds, http=http_client) # Uncomment if using Drive export
                logger.info("Google Sheets API service initialized successfully.")
            else:
                logger.warning("Google API credentials not available. API-based functions will fail.")
        except Exception as e:
            logger.error(f"Failed to initialize Google API services: {e}", exc_info=True)
            self.creds = None # Ensure creds is None if init fails

    @api_keys_required(
        [
            (None, "GOOGLE_CLIENT_ID"),
            (None, "GOOGLE_CLIENT_SECRET"),
            # GOOGLE_REFRESH_TOKEN is optional for the first run if interactive auth allowed
        ]
    )
    def _authenticate(self):
        creds = None
        client_id = os.environ.get('GOOGLE_CLIENT_ID')
        client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')
        refresh_token_env = os.environ.get('GOOGLE_REFRESH_TOKEN')
        token_uri = os.environ.get('GOOGLE_TOKEN_URI', 'https://oauth2.googleapis.com/token')
        
        # Define token_file_path for storing/retrieving token if interactive auth is used.
        # For CAMEL agents, it's best if GOOGLE_REFRESH_TOKEN is set.
        token_file_path = Path.home() / ".camel_google_sheets_token.json"

        if not client_id or not client_secret:
            logger.warning("GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET not set. Cannot authenticate.")
            return None

        # 1. Try using environment refresh token
        if refresh_token_env:
            logger.info("Attempting authentication using GOOGLE_REFRESH_TOKEN from environment.")
            creds = Credentials(
                None, refresh_token=refresh_token_env, token_uri=token_uri,
                client_id=client_id, client_secret=client_secret, scopes=SCOPES_SHEETS + SCOPES_DRIVE
            )
        # 2. Try loading from token file (if it exists from a previous interactive run)
        elif token_file_path.exists():
            logger.info(f"Attempting to load credentials from token file: {token_file_path}")
            try:
                creds = Credentials.from_authorized_user_file(str(token_file_path), SCOPES_SHEETS + SCOPES_DRIVE)
            except Exception as e:
                logger.warning(f"Failed to load token from {token_file_path}: {e}. Interactive auth may be needed.")
                creds = None
        
        # Validate and refresh credentials if obtained
        if creds:
            if not creds.valid:
                if creds.expired and creds.refresh_token:
                    logger.info("Refreshing Google API credentials.")
                    try:
                        creds.refresh(Request())
                        # Save refreshed token (especially if from token_file_path that had old refresh token)
                        if token_file_path.exists() or not refresh_token_env: # Only save if it was from file or no env var
                             with open(token_file_path, 'w') as token_file:
                                token_file.write(creds.to_json())
                             logger.info(f"Refreshed credentials saved to {token_file_path}")
                    except Exception as e:
                        logger.error(f"Failed to refresh Google API credentials: {e}. Interactive auth may be needed.")
                        creds = None # Force re-auth if refresh fails
                else: # Invalid but not expired or no refresh token (e.g. from_authorized_user_file failed somehow)
                    creds = None
            if creds and creds.valid:
                return creds
        
        # 3. Interactive flow (fallback, primarily for first-time setup by a user)
        # This should ideally not be hit by an autonomous agent in production.
        logger.info("No valid pre-existing credentials. Attempting interactive OAuth flow.")
        if os.environ.get("NO_INTERACTIVE_AUTH") == "1" or not sys.stdin.isatty():
            msg = ("Interactive OAuth flow required but disabled/unsupported. "
                   "Ensure GOOGLE_REFRESH_TOKEN is set, or run interactively once to generate a token file.")
            logger.error(msg)
            # raise EnvironmentError(msg) # Or just return None and let tools fail gracefully
            return None

        client_config = {
            "installed": {
                "client_id": client_id, "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": token_uri,
                "redirect_uris": ["http://localhost", "urn:ietf:wg:oauth:2.0:oob"],
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES_SHEETS + SCOPES_DRIVE)
        try:
            creds = flow.run_local_server(port=0)
        except OSError:
            logger.warning("Failed to start local server for OAuth, trying console flow.")
            creds = flow.run_console()
        
        if creds:
            with open(token_file_path, 'w') as token_file:
                token_file.write(creds.to_json())
            logger.info(f"Interactive authentication successful. Credentials saved to {token_file_path}")
            if creds.refresh_token:
                print(f"\n*** IMPORTANT: Your GOOGLE_REFRESH_TOKEN is: {creds.refresh_token} ***\n"
                      f"Set this as an environment variable for non-interactive use.\n")
        return creds

    def _extract_spreadsheet_id(self, sheet_url_or_id: str) -> Optional[str]:
        match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', sheet_url_or_id)
        if match:
            return match.group(1)
        if re.match(r'^[a-zA-Z0-9-_]{30,}$', sheet_url_or_id):
            return sheet_url_or_id
        logger.error(f"Invalid Google Sheet URL or ID format: {sheet_url_or_id}")
        return None

    def read_google_sheet_data(
        self,
        sheet_url_or_id: str,
        sheet_name: Optional[str] = None,
        data_range: Optional[str] = None,
        prefer_excel_export: bool = False, # New flag
    ) -> Union[Dict[str, Any], Dict[str, str]]:
        r"""Reads data from a Google Sheet using the Google Sheets API.

        Args:
            sheet_url_or_id (str): The URL of the Google Sheet or its ID.
            sheet_name (Optional[str]): Name of the sheet (tab). If None,
                reads from the first visible sheet.
            data_range (Optional[str]): A1 notation (e.g., "A1:C10"). If None
                and sheet_name provided, reads entire sheet.
            prefer_excel_export (bool): If True and Drive API service is
                available, attempts to export the sheet as .xlsx and parse
                that. Otherwise, uses Sheets API to get values directly.
                Default is False (use Sheets API values).

        Returns:
            Union[Dict[str, Any], Dict[str, str]]: Structured data or error.
        """
        if not self.creds or not self.sheets_service:
            msg = "Google API service not initialized or authentication failed. " \
                  "Ensure GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN " \
                  "are correctly set, or run interactively once. " \
                  "Alternatively, ensure the Google Sheet is public ('Anyone with the link can view') " \
                  "and try using a direct Excel export URL if available."
            logger.error(msg)
            return {"error": msg}

        spreadsheet_id = self._extract_spreadsheet_id(sheet_url_or_id)
        if not spreadsheet_id:
            return {"error": "Invalid Google Sheet URL or ID."}

        try:
            # Option 1: Export as .xlsx using Drive API (more robust for formatting)
            if prefer_excel_export and self.drive_service:
                logger.info(f"Attempting to export Google Sheet '{spreadsheet_id}' as .xlsx via Drive API.")
                request = self.drive_service.files().export_media(
                    fileId=spreadsheet_id,
                    mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    logger.info(f"Drive export download {int(status.progress() * 100)}%.")
                fh.seek(0)
                # Now use pandas to read this in-memory Excel file
                excel_file_obj = pd.ExcelFile(fh)
                # ... (rest of pandas parsing logic from previous toolkit)
                all_sheets_data = {}
                sheets_to_parse = [sheet_name] if sheet_name else excel_file_obj.sheet_names
                
                for s_name in sheets_to_parse:
                    if s_name not in excel_file_obj.sheet_names:
                        logger.warning(f"Sheet '{s_name}' not found in exported Excel. Available: {excel_file_obj.sheet_names}")
                        continue
                    df = excel_file_obj.parse(s_name) # No data_range here, parses whole sheet
                                                    # To support data_range with export, would need post-filtering
                    for col in df.select_dtypes(include=['datetime64[ns]', 'datetime64', 'datetimetz']).columns:
                        df[col] = df[col].astype(str)
                    for col in df.select_dtypes(include=['number']).columns:
                        df[col] = df[col].apply(lambda x: int(x) if pd.notnull(x) and x == int(x) else (float(x) if pd.notnull(x) else None))
                    df = df.astype(object).where(pd.notnull(df), None)
                    all_sheets_data[s_name] = df.to_dict(orient='records')
                if not all_sheets_data and sheets_to_parse:
                     return {"info": f"No data parsed from specified sheet(s) '{sheets_to_parse}' in exported Excel."}
                return {"info": f"Successfully processed {len(all_sheets_data)} sheet(s) via Drive export.", "data": all_sheets_data}


            # Option 2: Get values directly using Sheets API (default)
            if not sheet_name:
                sheet_metadata = self.sheets_service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id
                ).execute()
                sheets = sheet_metadata.get('sheets', [])
                if not sheets: return {"error": f"No sheets found in spreadsheet: {spreadsheet_id}"}
                first_visible_sheet = next((s for s in sheets if not s['properties'].get('hidden', False)), None)
                if not first_visible_sheet: return {"error": "No visible sheets found."}
                sheet_name = first_visible_sheet['properties']['title']
                logger.info(f"No sheet_name, using first visible: '{sheet_name}'")

            effective_range = f"'{sheet_name}'"
            if data_range:
                effective_range = f"'{sheet_name}'!{data_range}" if "!" not in data_range else data_range

            logger.info(f"Reading from Sheets API: id='{spreadsheet_id}', range='{effective_range}'")
            result = self.sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id, range=effective_range
            ).execute()
            values = result.get('values', [])

            if not values:
                return {"info": f"No data found in sheet '{sheet_name}' at range '{data_range or 'entire sheet'}'."}

            header = values[0]
            data_rows_values = values[1:]
            num_columns = len(header)
            parsed_data = [
                dict(zip(header, row + [None] * (num_columns - len(row)))) for row in data_rows_values
            ]
            return {"info": "Successfully read data via Sheets API.", "data": {sheet_name: parsed_data}} # Data for one sheet

        except Exception as e:
            # Check for specific Google API errors for better messages
            error_detail = str(e)
            if hasattr(e, 'resp') and hasattr(e.resp, 'status'):
                if e.resp.status == 401:
                    error_detail = "Unauthorized (401). Check credentials and sheet permissions."
                elif e.resp.status == 403:
                    error_detail = "Forbidden (403). Ensure Sheets API is enabled and user has permission for this sheet."
                elif e.resp.status == 404:
                    error_detail = f"Not Found (404). Spreadsheet ID '{spreadsheet_id}' or sheet/range may not exist."
            
            logger.error(f"Failed to read Google Sheet data for ID '{spreadsheet_id}': {error_detail}", exc_info=True)
            return {"error": f"Failed to read Google Sheet: {error_detail}"}

    def get_sheet_names(
        self,
        sheet_url_or_id: str,
    ) -> Union[List[str], Dict[str, str]]:
        r"""Retrieves the names of all sheets (tabs) in a Google Spreadsheet."""
        if not self.creds or not self.sheets_service: # Same check as read_google_sheet_data
             return {"error": "Google API service not initialized or auth failed."}
        spreadsheet_id = self._extract_spreadsheet_id(sheet_url_or_id)
        if not spreadsheet_id: return {"error": "Invalid Google Sheet URL or ID."}
        try:
            sheet_metadata = self.sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
            return [sheet['properties']['title'] for sheet in sheet_metadata.get('sheets', [])]
        except Exception as e:
            # Similar error detail extraction as above
            error_detail = str(e)
            if hasattr(e, 'resp') and hasattr(e.resp, 'status'):
                if e.resp.status == 403: error_detail = "Forbidden (403). Check API enabled & permissions."
            logger.error(f"Failed to get sheet names for ID '{spreadsheet_id}': {error_detail}", exc_info=True)
            return {"error": f"Failed to get sheet names: {error_detail}"}

    # The parse_markdown_table function can remain the same as in ExcelDocumentParsingToolkit
    # if you want this toolkit to also handle Markdown. Or it can be removed if this
    # toolkit is *only* for Google Sheets API interaction.
    # For this example, let's keep it to show a multi-purpose toolkit.
    def parse_markdown_table(
        self,
        markdown_content: str,
    ) -> Union[List[Dict[str, Any]], Dict[str, str]]:
        # ... (Exact same implementation as in the previous ExcelDocumentParsingToolkit) ...
        lines = markdown_content.strip().split('\n')
        data_rows: List[Dict[str, Any]] = []
        header_line_index = -1
        header: List[str] = []
        table_found = False
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if line_stripped.startswith('|') and line_stripped.endswith('|') and line_stripped.count('|') > 1:
                if i + 1 < len(lines):
                    next_line_stripped = lines[i+1].strip()
                    if next_line_stripped.startswith('|') and next_line_stripped.endswith('|') and \
                       all(c in '|-: ' for c in next_line_stripped.replace('|', '')):
                        potential_header_parts = [h.strip() for h in line_stripped.split('|')[1:-1]]
                        separator_parts = [s.strip() for s in next_line_stripped.split('|')[1:-1]]
                        if len(potential_header_parts) == len(separator_parts) and \
                           all(re.match(r':?---+:?', part) for part in separator_parts if part.strip()):
                            header = potential_header_parts; header_line_index = i; table_found = True
                            logger.info(f"Found Markdown table header: {header}"); break
        if not table_found or not header:
            return {"info": "No standard Markdown table found."}
        for line_number, line_content in enumerate(lines[header_line_index + 2:]):
            line_stripped = line_content.strip()
            if not line_stripped.startswith('|') or not line_stripped.endswith('|'):
                if data_rows: break
                continue
            values = [v.strip() for v in line_stripped.split('|')[1:-1]]
            if len(values) != len(header):
                logger.warning(f"Skipping MD row due to col mismatch. Expected {len(header)}, got {len(values)}."); continue
            data_rows.append({h: (None if v.lower() == 'nan' else v) for h, v in zip(header, values)})
        if not data_rows and table_found: return {"info": "MD table header found, but no data rows."}
        return data_rows


    def get_tools(self) -> List[FunctionTool]:
        return [
            FunctionTool(self.read_google_sheet_data),
            FunctionTool(self.get_sheet_names),
            FunctionTool(self.parse_markdown_table), # If kept
        ]

# --- Example Usage (for testing the toolkit directly) ---
if __name__ == "__main__":
    import sys # For sys.stdin.isatty() in _authenticate
    from httplib2 import Http # For TimeoutHttp in _authenticate if needed

    # IMPORTANT: For this to run, set environment variables:
    # GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    # And ideally GOOGLE_REFRESH_TOKEN (obtained from a first interactive run)
    # Ensure the Google Sheet is accessible by the authenticated account.
    print("Ensure GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN (recommended) are set.")

    toolkit = GoogleSheetsAPIToolkit() # Timeout can be passed here

    if not toolkit.creds:
        print("\n--- Google API Authentication Failed or Not Configured ---")
        print("Please set up Google Cloud credentials and environment variables.")
        print("Or run this script once interactively to generate a token file and refresh token.")
    else:
        print("\n--- Testing GoogleSheetsAPIToolkit ---")
        # Replace with a Google Sheet URL or ID you have access to
        test_sheet_url = input("Enter a Google Sheet URL or ID for testing: ")
        if not test_sheet_url:
            print("No sheet URL provided. Exiting test.")
            exit()

        print(f"\n1. Getting sheet names for: {test_sheet_url}")
        sheet_names = toolkit.get_sheet_names(test_sheet_url)
        print("Sheet Names:", sheet_names)

        if isinstance(sheet_names, list) and sheet_names:
            first_sheet_name = sheet_names[0]
            print(f"\n2. Reading data from first sheet ('{first_sheet_name}') using Sheets API values:")
            data_api = toolkit.read_google_sheet_data(
                sheet_url_or_id=test_sheet_url,
                sheet_name=first_sheet_name
            )
            if isinstance(data_api, dict) and "data" in data_api:
                import json
                print(json.dumps(data_api, indent=2))
            else:
                print("Error or no data:", data_api)

            # Optional: Test Drive API export (if you've enabled Drive API and uncommented its service init)
            # if toolkit.drive_service:
            #     print(f"\n3. Reading ALL data from sheet via Drive API export (.xlsx):")
            #     data_drive_export = toolkit.read_google_sheet_data(
            #         sheet_url_or_id=test_sheet_url,
            #         prefer_excel_export=True
            #     )
            #     if isinstance(data_drive_export, dict) and "data" in data_drive_export:
            #         import json
            #         print(json.dumps(data_drive_export, indent=2))
            #     else:
            #         print("Error or no data from Drive export:", data_drive_export)
            # else:
            #     print("\nDrive service not initialized, skipping Drive export test.")

    print("\n--- Testing Markdown Parsing (independent of Google API) ---")
    md_test_content = "| Name | Age |\n|------|-----|\n| Alice | 30  |\n| Bob   | 24  |"
    parsed_md = toolkit.parse_markdown_table(md_test_content)
    print("Parsed Markdown:", parsed_md)