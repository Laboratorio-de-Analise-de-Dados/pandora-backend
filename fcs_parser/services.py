import os
from io import BytesIO
import json
from django.http import HttpResponse, HttpResponseBadRequest
from fcsparser import parse
from pandas import DataFrame

FILEPATH = 'loadedData'

def serialize_value(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8')
    elif isinstance(value, dict):
        return {key: serialize_value(val) for key, val in value.items()}
    else:
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)

def process_fcs_file(fcs_file):
    try:
        experiment_directory = f'./assets/{FILEPATH}.fcs'
        os.makedirs(os.path.dirname(experiment_directory), exist_ok=True)
        with open(experiment_directory, 'wb') as f:
            for chunk in fcs_file.chunks():
                f.write(chunk)
        headers, data_set = parse(experiment_directory)
        data_set['id'] = range(1, len(data_set) + 1)
        data_set.columns = data_set.columns.str.replace(' ', '_')
        print(type(data_set))
        json_dataset = data_set.to_json(orient='records')
        serialized_header = {key: serialize_value(value) for key, value in headers.items()}
       
        json_header = json.dumps(serialized_header, indent=2)

        result_json = {
            "headers": json_header,
            "values": json.loads(json_dataset)
        }
        result_json_str = json.dumps(result_json, indent=2)
        response = HttpResponse(result_json_str, content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename="{FILEPATH}.json"'
        return response

    except Exception as e:
        return HttpResponseBadRequest(f'Error processing FCS file: {str(e)}')
    finally:
        if os.path.exists(experiment_directory):
            os.remove(experiment_directory)
