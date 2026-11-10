"""Dataset loading helpers shared by all pages."""

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from core.schemas import detect_schema
from core.path_manager import find_local_system_path, save_persistent_path
from core.sample_data import generate_sample_datasets


def load_dataset_from_source(source, expected_schema=None, slot_key=None):
    """
    Loads dataset from a local file path (str or Path) or uploaded file object.
    If the file exists on the local filesystem, saves the path, avoids holding large
    file_bytes in memory, and persists the path across app restarts.
    """
    file_path = None
    file_name = ""
    file_bytes = None

    if isinstance(source, (str, Path)):
        resolved = Path(source).expanduser().resolve()
        if resolved.exists() and resolved.is_file():
            file_path = resolved
            file_name = resolved.name
        else:
            st.error(f"File not found on system: {source}")
            return False
    elif hasattr(source, "name"):
        file_name = source.name
        # Check if this uploaded file exists on the local system
        local_match = find_local_system_path(file_name)
        if local_match:
            file_path = local_match
        else:
            file_bytes = source.getvalue()
    else:
        return False

    try:
        schema = detect_schema(file_name)
        if schema == "Custom / Unknown" and expected_schema:
            schema = expected_schema

        if file_path:
            if file_name.endswith((".xlsx", ".xls")):
                excel_file = pd.ExcelFile(file_path)
                sheet_names = excel_file.sheet_names
                pref_sheet = sheet_names[0]
                for s in sheet_names:
                    if any(k in s.lower() for k in ["server", "sheet", "data"]):
                        pref_sheet = s
                        break
                df = pd.read_excel(excel_file, sheet_name=pref_sheet)
                is_excel = True
            else:
                # CSV files may come from Windows/Excel with different encodings
                encodings = ["utf-8-sig", "utf-8", "cp1252", "latin1"]
        
                last_error = None
        
                for encoding in encodings:
                    try:
                        df = pd.read_csv(file_path, encoding=encoding)
                        break
                    except UnicodeDecodeError as e:
                        last_error = e
                else:
                    raise ValueError(
                        f"Unable to decode CSV file '{file_name}'. "
                        f"Tried encodings: {', '.join(encodings)}"
                    ) from last_error
        
                sheet_names = ["default"]
                pref_sheet = "default"
                is_excel = False

            # Persist path if slot_key provided or derivable
            derived_slot = slot_key
            if not derived_slot:
                if schema == "ARM SERVICE NOW FILE":
                    derived_slot = "arm"
                elif schema == "BA-AS-A-SERVER":
                    derived_slot = "ba"
                elif schema == "CMDB-CI-SERVER":
                    derived_slot = "cmdb"
            if derived_slot:
                save_persistent_path(derived_slot, str(file_path))

            st.session_state.datasets[file_name] = {
                "file_path": str(file_path),
                "file_bytes": None,
                "df": df,
                "detected_schema": schema,
                "sheets": sheet_names,
                "active_sheet": pref_sheet,
                "is_excel": is_excel,
                "is_local": True
            }
            return True
        else:
            # In-memory uploaded bytes fallback
            if file_name.endswith((".xlsx", ".xls")):
                excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
                sheet_names = excel_file.sheet_names
                pref_sheet = sheet_names[0]
                for s in sheet_names:
                    if any(k in s.lower() for k in ["server", "sheet", "data"]):
                        pref_sheet = s
                        break
                df = pd.read_excel(excel_file, sheet_name=pref_sheet)
                is_excel = True
            else:
                df = pd.read_csv(io.BytesIO(file_bytes))
                sheet_names = ["default"]
                pref_sheet = "default"
                is_excel = False

            st.session_state.datasets[file_name] = {
                "file_path": None,
                "file_bytes": file_bytes,
                "df": df,
                "detected_schema": schema,
                "sheets": sheet_names,
                "active_sheet": pref_sheet,
                "is_excel": is_excel,
                "is_local": False
            }
            return True
    except Exception as e:
        st.error(f"Error loading {file_name}: {e}")
        return False


def load_file_into_state(file_name, file_bytes, expected_schema=None):
    """Compatibility wrapper calling load_dataset_from_source."""
    class DummyUpload:
        def __init__(self, name, bytes_val):
            self.name = name
            self._bytes = bytes_val
        def getvalue(self):
            return self._bytes
    return load_dataset_from_source(DummyUpload(file_name, file_bytes), expected_schema=expected_schema)


def load_sample_data():
    """Populates session state with mock datasets for testing."""
    samples = generate_sample_datasets()
    st.session_state.datasets.clear()
    for name, df in samples.items():
        schema = detect_schema(name)
        st.session_state.datasets[name] = {
            "df": df,
            "detected_schema": schema,
            "sheets": ["Sheet1"],
            "active_sheet": "Sheet1",
            "is_excel": True,
            "file_bytes": None
        }


def convert_df_to_excel_bytes(dfs_dict):
    """Converts a dict of {sheet_name: df} into an in-memory Excel workbook."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, df in dfs_dict.items():
            safe_name = str(sheet_name)[:31].replace(":", "_").replace("/", "_").replace("\\", "_")
            df.to_excel(writer, sheet_name=safe_name, index=False)
    output.seek(0)
    return output.getvalue()
