from fcsparser import parse
import json
from .header_parser import serialize_value


def process_fcs_file(fcs_file_path: str):
    try:
        headers, data_set = parse(fcs_file_path)
        data_set["id"] = range(1, len(data_set) + 1)

        json_dataset = data_set.to_json(orient="records")
        serialized_header = transform_header(headers)

        json_header = json.dumps(serialized_header, indent=2)

        values = data_set.columns.tolist()
        return [json_header, json.loads(json_dataset), values]

    except Exception as e:
        return f"Error processing FCS file: {str(e)}"


def transform_key(key):
    return key.replace("_", "").replace(" ", "_").lower()


def transform_header(headers):
    header = {}
    for key, value in headers.items():
        header[transform_key(key)] = serialize_value(value)
    return header
