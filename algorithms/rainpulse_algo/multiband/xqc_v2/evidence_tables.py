"""Lossless JSON record tables; never truncate scientific evidence to fit."""
FORMAT = 'xqc-record-table-v1'


def compact(value):
    if isinstance(value, dict):
        return {key: compact(item) for key, item in value.items()}
    if not isinstance(value, list):
        return value
    if len(value) >= 2 and all(isinstance(item, dict) for item in value):
        columns = list(value[0])
        if columns and all(set(item) == set(columns) for item in value):
            return {'encoding': FORMAT, 'columns': columns,
                    'rows': [[compact(item[key]) for key in columns] for item in value]}
    return [compact(item) for item in value]


def expand(value):
    if isinstance(value, list):
        return [expand(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get('encoding') == FORMAT and set(value) == {'encoding', 'columns', 'rows'}:
        columns = value['columns']
        if len(set(columns)) != len(columns) or any(len(row) != len(columns) for row in value['rows']):
            raise ValueError('invalid X QC evidence record table')
        return [{key: expand(item) for key, item in zip(columns, row)} for row in value['rows']]
    return {key: expand(item) for key, item in value.items()}
