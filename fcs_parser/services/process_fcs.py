from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import readfcs

from .header_parser import serialize_value

logger = logging.getLogger(__name__)


@dataclass
class FCSResult:
    """Typed container for the output of :func:`process_fcs_file`."""

    headers: dict = field(default_factory=dict)
    data: list = field(default_factory=list)
    channels: list = field(default_factory=list)


def process_fcs_file(fcs_file_path: str) -> FCSResult:
    """Parse a single .fcs file and return structured data.

    Raises ``ValueError`` on any processing error instead of returning
    a bare error string.
    """
    try:
        headers, _ = readfcs.view(fcs_file_path)
        fcsfile = readfcs.ReadFCS(fcs_file_path)
        data_set = fcsfile.data
        channels = fcsfile.channels
        data_set.columns = channels["PnN"].tolist()

        data_set["id"] = range(1, len(data_set) + 1)

        json_dataset = data_set.to_json(orient="records")

        values = data_set.columns.tolist()
        return FCSResult(
            headers=headers,
            data=json.loads(json_dataset),
            channels=values,
        )

    except Exception as e:
        raise ValueError(f"Error processing FCS file: {e}") from e


def transform_key(key):
    return key.replace("_", "").replace(" ", "_").lower()


def transform_header(headers):
    header = {}
    for key, value in headers.items():
        header[transform_key(key)] = serialize_value(value)
    return header
