import json

def serialize_value(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8')
    elif isinstance(value, dict):
        return {key.replace("_", "").replace(" ", "_"): serialize_value(val) for key, val in value.items()}
    else:
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)
