from fcsparser import parse
import json
from .header_parser import serialize_value


def process_fcs_file(fcs_file_path: str):
    try:
        with open(fcs_file_path, 'rb') as f:
            headers, data_set = parse(fcs_file_path)
            data_set['id'] = range(1, len(data_set) + 1)
            

            json_dataset = data_set.to_json(orient='records')
            serialized_header = {key.replace("_", "").replace(" ", "_").lower(): serialize_value(value) for key, value in headers.items()}

            json_header = json.dumps(serialized_header, indent=2)
            
            values = data_set.columns.tolist()
            return [json_header, json.loads(json_dataset), values]

    except Exception as e:
        return f'Error processing FCS file: {str(e)}'