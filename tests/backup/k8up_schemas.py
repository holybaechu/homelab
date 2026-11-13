"""Expose the pinned official chart's CRD schemas to kubeconform."""
import json
from pathlib import Path
import subprocess
import sys

def reject_unknown_fields(node):
    if isinstance(node, dict):
        if node.get('type') == 'object' and 'properties' in node and not node.get('x-kubernetes-preserve-unknown-fields'):
            node.setdefault('additionalProperties', False)
        for value in node.values():
            reject_unknown_fields(value)
    elif isinstance(node, list):
        for value in node:
            reject_unknown_fields(value)


chart, output = map(Path, sys.argv[1:])
output.mkdir(parents=True, exist_ok=True)
for path in sorted((chart / 'crds').glob('*.yaml')):
    crd = json.loads(subprocess.check_output(['yq', '-o=json', '.', str(path)]))
    for version in crd['spec']['versions']:
        if version['served']:
            schema = version['schema']['openAPIV3Schema']
            # Reject fields the API server would silently prune.
            reject_unknown_fields(schema)
            (output / (crd['spec']['names']['kind'].lower() + '.json')).write_text(json.dumps(schema))
