"""Merge node — a branch e a main divergiram em 0021:

- branch: 0022_filedatamodel_control_channel_and_more (BE-26, controles
  por amostra)
- main:   0022_relative_media_paths → 0023_filemodel_upload_status
  (#112 backport + BE-42 upload status)

A `0023_relative_media_paths` que existia na branch era a mesma
migration que a main recebeu como `0022_relative_media_paths` (backport
da dívida #112) — removida no merge para não duplicar.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("fcs_parser", "0022_filedatamodel_control_channel_and_more"),
        ("fcs_parser", "0023_filemodel_upload_status"),
    ]

    operations = []
