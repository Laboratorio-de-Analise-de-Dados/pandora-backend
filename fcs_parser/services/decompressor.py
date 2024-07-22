import zipfile
import os


def decompres_file(file_path, target_path: str):
    try:
        os.makedirs(target_path, exist_ok=True)
        with zipfile.ZipFile(file_path, "r") as zip_ref:
            zip_ref.extractall(target_path)

        return target_path
    except Exception as e:
        return f"Error processing ZIP file: {str(e)}"
