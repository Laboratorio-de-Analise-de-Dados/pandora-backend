# import os
# import shutil
# from fcs_parser.services import process_fcs_file
# from fcs_parser.services.decompressor import decompres_file
# from django.conf import settings

# def process_zip(zip_file, directory_name:str):
#   try:
#     directory_path = os.path.join(settings.BASE_DIR, 'assets', 'fcs_files', directory_name)
   
#     os.makedirs(directory_path, exist_ok=True)
#     decompres_path = decompres_file(zip_file, directory_path)
#     for file_name in os.listdir(decompres_path):
#       if file_name.endswith(".fcs"):
#         complete_path: str = os.path.join(decompres_path, file_name)
#         processed_files = process_fcs_file(complete_path, ['FSC-A', 'SSC-A'])
        
#     return processed_files
#   except Exception as e:
#       raise e
#   finally:
#       shutil.rmtree(decompres_path, ignore_errors=True)
