from django.core.exceptions import ValidationError
import os 
MB = 30
MAX_SIZE = MB * 1024 * 1024


def validate_zip_file(file):
    ext = os.path.splitext(file.name)[1]
    if ext.lower() != '.zip':
        raise ValidationError('O arquivo deve ser um arquivo ZIP.')

def validate_file_size(file):
  """Validation file size function, take a file as argument"""

  dir(file)
  print(file.size)
  if file.size > MAX_SIZE:
      raise ValidationError(f"File exceed maximum size {MB}mb")
