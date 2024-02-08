from django.db import models

from django.core.exceptions import ValidationError

MB = 5
MAX_SIZE = MB * 1024 * 1024


def validate_file_size(file):
  """Validation file size function, take a file as argument"""

  dir(file)
  if file.size > MAX_SIZE:
      raise ValidationError(f"File exceed maximum size {MB}mb")

class FCSFile(models.Model):
    """Model for FCS file"""

    title = models.CharField(max_length=50, unique=True)
    file = models.FileField(upload_to='fcs_files/', validators=[validate_file_size])


