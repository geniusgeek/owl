# excel_document_parsing_toolkit.py

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse, unquote

import pandas as pd # For Excel processing

from camel.logger import get_logger
from camel.toolkits.base import BaseToolkit
from camel.toolkits import FunctionTool
from camel.utils import MCPServer

logger = get_logger(__name__)

# pandas typically needs an engine for .xlsx files
# This try-except block is for user guidance if the dependency is missing.
try:
    import openpyxl
except ImportError:
    logger.warning(
        "The 'openpyxl' library is not installed. "
        "It is required for reading .xlsx Excel files. "
        "Please install it using: pip install openpyxl"
    )


@MCPServer()
class GoogleSheetBrowserBaseToolkit(BaseToolkit):
    r"""A toolkit for processing Google Sheets documents and parsing Markdown tables.

    This toolkit uses pandas to read data from Google Sheets files (local or direct URLs)
    and provides a generic parser for the first standard Markdown table found
    in a given text.

    It assumes that input files/URLs are directly accessible without complex
    authentication that pandas cannot handle out-of-the-box. For sources like
    Google Sheets requiring OAuth, the content should be pre-downloaded as an
    .xlsx file or pre-converted to Markdown by another specialized tool.
    """

    def __init__(
        self,
        timeout: Optional[float] = None, # Standard BaseToolkit parameter
    ):
        r"""Initializes the GoogleSheetBrowserBaseToolkit.

        Args:
            timeout (Optional[float]): A timeout value for operations, if
                applicable. (Less critical for these local/direct operations
                but included for consistency with BaseToolkit).
        """
        super().__init__(timeout=timeout)
        # No external services to initialize for this toolkit.

    def read_data_from_googlesheet_url(
        self,
        file_path_or_url: str,
    ) -> Union[Dict[str, Any], Dict[str, str]]: # Return type includes overall status/data
        r"""Reads an Google Sheet Excel file and converts all its sheets into a structured
        JSON-like dictionary.

        The Google Sheet file can be specified by a local file path or a direct URL
        that pandas can access (e.g., a public link to an .xlsx/.xls file).
        This function will process all sheets, columns, and rows within the
        Google Sheet file.

        Args:
            file_path_or_url (str): The local path to the Google Sheet file or a
                direct URL (e.g., "http://example.com/data.xlsx",
                "file:///path/to/data.xlsx").

        Returns:
            Union[Dict[str, Any], Dict[str, str]]:
            On success, a dictionary with an "info" key and a "data" key.
            The "data" key holds another dictionary where keys are sheet names
            and values are lists of row dictionaries (column_header: value).
            On failure, a dictionary with an "error" key and a message.
            Example successful output:
            {
                "info": "Successfully processed 2 sheet(s).",
                "data": {
                    "Sheet1": [{"ColA": 1, "ColB": "X"}, {"ColA": 2, "ColB": "Y"}],
                    "Sheet2": [{"Foo": "Bar"}]
                }
            }
        """
        try:
            xls_input: Union[str, Path] = file_path_or_url
            parsed_uri = urlparse(file_path_or_url)

            # Handle 'file://' URIs by converting them to local paths
            if parsed_uri.scheme == 'file':
                if os.name == 'nt' and parsed_uri.path.startswith('/') and parsed_uri.netloc:
                    path_str = f"{parsed_uri.netloc}{unquote(parsed_uri.path)}"
                elif os.name == 'nt' and parsed_uri.path.startswith('/'):
                     path_str = unquote(parsed_uri.path[1:])
                else:
                    path_str = unquote(parsed_uri.path)
                xls_input = Path(path_str)
                if not xls_input.exists():
                    logger.error(f"Excel file path from URI does not exist: {xls_input}")
                    return {"error": f"File not found at path from URI: {xls_input}"}
            elif not parsed_uri.scheme and Path(file_path_or_url).exists():
                xls_input = Path(file_path_or_url)
            # Otherwise, pandas will attempt to treat it as a URL or handle path.

            logger.info(f"Attempting to read Google Sheet from: {xls_input}")
            excel_file_obj = pd.ExcelFile(xls_input) # Reads the whole workbook
            all_sheets_data: Dict[str, List[Dict[str, Any]]] = {}

            if not excel_file_obj.sheet_names:
                logger.warning(f"No sheets found in Google Sheet file: {file_path_or_url}")
                return {"info": f"No sheets found in {file_path_or_url}", "data": {}}

            # Iterate through all sheets in the Excel file
            for sheet_name in excel_file_obj.sheet_names:
                logger.info(f"Processing sheet: {sheet_name}")
                df = excel_file_obj.parse(sheet_name) # Parses the current sheet

                # Convert pandas dtypes to JSON-serializable types
                for col in df.select_dtypes(include=['datetime64[ns]', 'datetime64', 'datetimetz']).columns:
                    df[col] = df[col].astype(str)
                for col in df.select_dtypes(include=['number']).columns:
                    df[col] = df[col].apply(
                        lambda x: int(x) if pd.notnull(x) and x == int(x) else (float(x) if pd.notnull(x) else None)
                    )
                df = df.astype(object).where(pd.notnull(df), None) # Convert NaN/NaT to None
                all_sheets_data[sheet_name] = df.to_dict(orient='records')

            return {"info": f"Successfully processed {len(all_sheets_data)} sheet(s).", "data": all_sheets_data}

        except FileNotFoundError:
            logger.error(f"Google Sheet file not found: {file_path_or_url}")
            return {"error": f"Google Sheet file not found: {file_path_or_url}"}
        except ValueError as ve:
            logger.error(f"Could not parse Google Sheet file {file_path_or_url}. Details: {ve}")
            return {"error": f"Could not parse Google Sheet file {file_path_or_url}. It might be an invalid format, inaccessible URL, or require unsupported authentication. Details: {ve}"}
        except Exception as e:
            logger.error(f"Unexpected error processing Google Sheet file {file_path_or_url}: {e}", exc_info=True)
            return {"error": f"Unexpected error processing Google Sheet file: {e}"}

    def parse_markdown_table(
        self,
        markdown_content: str,
    ) -> Union[List[Dict[str, Any]], Dict[str, str]]:
        r"""Parses the first standard Markdown table found in a block of text.

        This function identifies a table by its header and separator lines
        (e.g., | H1 | H2 | \n |---|----|). It extracts all columns and rows
        from that first identified table.

        Args:
            markdown_content (str): A string containing Markdown text.

        Returns:
            Union[List[Dict[str, Any]], Dict[str, str]]:
            If a table is found and parsed, a list of dictionaries, where each
            dictionary represents a row (column_header: value).
            If no table is found or an issue occurs, a dictionary with an
            "info" or "error" key and a descriptive message.
        """
        lines = markdown_content.strip().split('\n')
        data_rows: List[Dict[str, Any]] = []
        header_line_index = -1
        header: List[str] = []
        table_found = False

        for i, line in enumerate(lines):
            line_stripped = line.strip()
            # Check for a potential table header row
            if line_stripped.startswith('|') and line_stripped.endswith('|') and line_stripped.count('|') > 1:
                if i + 1 < len(lines): # Check if there's a next line for the separator
                    next_line_stripped = lines[i+1].strip()
                    # Check if the next line looks like a Markdown table separator
                    if next_line_stripped.startswith('|') and next_line_stripped.endswith('|') and \
                       all(c in '|-: ' for c in next_line_stripped.replace('|', '')):

                        potential_header_parts = [h.strip() for h in line_stripped.split('|')[1:-1]]
                        separator_parts = [s.strip() for s in next_line_stripped.split('|')[1:-1]]

                        # Validate header and separator structure
                        if len(potential_header_parts) == len(separator_parts) and \
                           all(re.match(r':?---+:?', part) for part in separator_parts if part.strip()):
                            header = potential_header_parts
                            header_line_index = i
                            table_found = True
                            logger.info(f"Found Markdown table header: {header}")
                            break # Process only the first table found
        
        if not table_found or not header:
            logger.info("No standard Markdown table found in the content.")
            return {"info": "No standard Markdown table found in the provided content."}

        # Process data rows for the identified table
        for line_number, line_content in enumerate(lines[header_line_index + 2:]):
            line_stripped = line_content.strip()
            # Check if the line is part of the table structure
            if not line_stripped.startswith('|') or not line_stripped.endswith('|'):
                if data_rows: # If we were parsing rows, this signifies end of table
                    break
                continue # Skip non-table lines before table data starts or after it ends

            values = [v.strip() for v in line_stripped.split('|')[1:-1]]
            
            if len(values) != len(header):
                logger.warning(
                    f"Skipping Markdown row (approx content line {header_line_index + 3 + line_number}) "
                    f"due to column mismatch. Expected {len(header)}, got {len(values)}. Row: '{line_stripped}'"
                )
                continue

            row_data = {h: (None if v.lower() == 'nan' else v) for h, v in zip(header, values)}
            data_rows.append(row_data)
        
        if not data_rows and table_found: # Header found but no data rows
            return {"info": "Markdown table header found, but no data rows followed or parsed."}
            
        return data_rows # Returns list of dicts or empty list if header found but no rows

    def get_tools(self) -> List[FunctionTool]:
        r"""Returns a list of FunctionTool objects representing the
        callable functions within this toolkit. This is how the CAMEL
        framework discovers the toolkit's capabilities.
        """
        return [
            FunctionTool(self.read_data_from_googlesheet_url),
            FunctionTool(self.parse_markdown_table),
        ]

# --- Example Usage (for testing the toolkit directly) ---
if __name__ == "__main__":
    toolkit = GoogleSheetBrowserBaseToolkit()
    # Create a dummy logger for simple console output if not run under CAMEL's logger setup
    if not logger.hasHandlers(): # Check if CAMEL's logger (or any other) is already configured
        import logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


    print("\n======== TESTING GoogleSheetBrowserBaseToolkit ========")

    # --- Test 1: Excel Processing ---
    print("\n--- Test 1: Excel Processing ---")
    dummy_excel_path = "temp_test_excel_file.xlsx"
    try:
        data_sheet1 = {'ID': [1, 2, 3], 'Name': ['Alice', 'Bob', 'Charlie'], 'Value': [10.5, 20.0, None]}
        df_sheet1 = pd.DataFrame(data_sheet1)
        data_sheet2 = {'Product': ['Apple', 'Banana'], 'Price': [0.5, 0.25], 'InStock': [True, False]}
        df_sheet2 = pd.DataFrame(data_sheet2)

        with pd.ExcelWriter(dummy_excel_path, engine='openpyxl') as writer:
            df_sheet1.to_excel(writer, sheet_name='Orders', index=False)
            df_sheet2.to_excel(writer, sheet_name='Inventory', index=False)
        print(f"Created dummy Excel file for testing: {dummy_excel_path}")

        # Test with local file path
        print(f"\nReading local Excel file: {dummy_excel_path}")
        excel_data_local = toolkit.read_data_from_googlesheet_url(dummy_excel_path)
        if isinstance(excel_data_local, dict) and "data" in excel_data_local:
            import json # For pretty printing
            print("Successfully read local Excel data:")
            print(json.dumps(excel_data_local, indent=2))
        else:
            print("Error or unexpected result reading local Excel:")
            print(excel_data_local)

        # Test with a non-existent file
        non_existent_file = "this_file_does_not_exist.xlsx"
        print(f"\nReading non-existent Google Sheet file: {non_existent_file}")
        error_data = toolkit.read_data_from_googlesheet_url(non_existent_file)
        print(error_data)
        
        # Test with a Google Sheet URL (expected to fail gracefully by pandas or network error)
        # Pandas cannot directly authenticate/parse these without specialized handling
        gsheet_url_example = "https://docs.google.com/spreadsheets/d/some_fake_id_for_testing/edit"
        print(f"\nAttempting to read Google Sheet URL (expected to fail): {gsheet_url_example}")
        gsheet_error_result = toolkit.read_data_from_googlesheet_url(gsheet_url_example)
        print(gsheet_error_result)

    finally:
        if os.path.exists(dummy_excel_path):
            os.remove(dummy_excel_path)
            print(f"\nRemoved dummy Google Sheet file: {dummy_excel_path}")

    # --- Test 2: Markdown Table Parsing ---
    print("\n\n--- Test 2: Markdown Table Parsing ---")
    markdown_with_table = """
    ## Report Summary

    Here is the data from our latest survey:

    | User ID | Engagement Score | Feedback Type |
    |---------|------------------|---------------|
    | usr001  | 85               | Positive      |
    | usr002  | 40               | Neutral       |
    | usr003  | 92               | Positive      |
    | usr004  | nan              | Bug Report    |

    Please review the details above.
    """
    print("\nParsing Markdown with a table:")
    parsed_md_data = toolkit.parse_markdown_table(markdown_with_table)
    if isinstance(parsed_md_data, list):
        print("Successfully parsed Markdown table:")
        for row in parsed_md_data:
            print(row)
    else:
        print("Error or unexpected result parsing Markdown:")
        print(parsed_md_data)

    markdown_without_table = "This is a sample text. It contains no Markdown tables."
    print("\nParsing Markdown without a table:")
    no_table_result = toolkit.parse_markdown_table(markdown_without_table)
    print(no_table_result)

    markdown_header_only = "| Column1 | Column2 |\n|---------|---------|"
    print("\nParsing Markdown with header only:")
    header_only_result = toolkit.parse_markdown_table(markdown_header_only)
    print(header_only_result)

    print("\n\n======== Toolkit Tools Discovery ========")
    tools_list = toolkit.get_tools()
    print(f"Discovered {len(tools_list)} tools in the toolkit:")
    for tool_func in tools_list:
        print(f" - Tool Name: {tool_func.name}, Description: {tool_func.description.splitlines()[0]}...") # First line of desc

    print("\n======== TESTING COMPLETE ========")