"""Minimal JSON-RPC app-server fixture used by contract tests."""
import json
import sys


for line in sys.stdin:
    request = json.loads(line)
    if request.get('method') == 'initialize':
        print(json.dumps({'id': request['id'], 'result': {}}), flush=True)
    elif request.get('method') == 'account/rateLimits/read':
        print(json.dumps({'method': 'account/rateLimits/updated', 'params': {
            'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': 33, 'windowDurationMins': 300}}}}), flush=True)
        print(json.dumps({'id': request['id'], 'result': {
            'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': 25, 'windowDurationMins': 300}}}}), flush=True)
