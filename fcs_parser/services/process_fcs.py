import json
import readfcs
from .header_parser import serialize_value


def process_fcs_file(fcs_file_path: str):
    try:
        print(fcs_file_path)
        headers, _ = readfcs.view(fcs_file_path)
        fcsfile = readfcs.ReadFCS(fcs_file_path)
        data_set = fcsfile.data
        channels = fcsfile.channels 
        data_set.columns = channels["PnN"].tolist()
        
        data_set["id"] = range(1, len(data_set) + 1)

        json_dataset = data_set.to_json(orient="records")

        values = data_set.columns.tolist()
        return [headers, json.loads(json_dataset), values]

    except Exception as e:
        return f"Error processing FCS file: {str(e)}"


def transform_key(key):
    return key.replace("_", "").replace(" ", "_").lower()


def transform_header(headers):
    header = {}
    for key, value in headers.items():
        header[transform_key(key)] = serialize_value(value)
    return header
