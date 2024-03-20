import zipfile
import os

def decompres_file(file, target_path:str):
  try:
    os.makedirs(target_path, exist_ok=True)
    with zipfile.ZipFile(file, 'r') as zip_ref:
      zip_ref.extractall(target_path)
    
    return target_path
  except Exception as e:
    return f'Error processing ZIP file: {str(e)}'