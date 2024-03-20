from fcsparser import parse
import json
from .header_parser import serialize_value


def process_fcs_file(fcs_file_path: str):
    try:
        with open(fcs_file_path, 'rb') as f:
            headers, data_set = parse(fcs_file_path)
            print(data_set)
            data_set['id'] = range(1, len(data_set) + 1)
            # data_set.columns = data_set.columns.str.replace(' ', '')
            # data_set.columns = data_set.columns.str.replace('-', '_')
            # data_set.columns = data_set.columns.str.lower()

            json_dataset = data_set.to_json(orient='records')
            serialized_header = {key.replace("_", "").replace(" ", "_").lower(): serialize_value(value) for key, value in headers.items()}

            json_header = json.dumps(serialized_header, indent=2)

            return [json_header, json.loads(json_dataset)]

    except Exception as e:
        return f'Error processing FCS file: {str(e)}'